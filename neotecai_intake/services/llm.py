"""Thin client for LocalAI's OpenAI-compatible chat API."""

import json
import re

import frappe
import requests
from frappe import _


class LLMError(Exception):
	pass


def get_settings():
	settings = frappe.get_cached_doc("NeotecAI Intake Settings")
	if not settings.enabled:
		frappe.throw(_("NeotecAI Intake is disabled in NeotecAI Intake Settings"))
	return settings


def _headers(settings):
	headers = {"Content-Type": "application/json"}
	key = settings.get_password("api_key", raise_exception=False) if settings.api_key else None
	if key:
		headers["Authorization"] = f"Bearer {key}"
	return headers


def _url(settings, path):
	return settings.base_url.rstrip("/") + path


def list_models(settings=None):
	settings = settings or get_settings()
	r = requests.get(_url(settings, "/v1/models"), headers=_headers(settings), timeout=15)
	r.raise_for_status()
	return [m.get("id") for m in r.json().get("data", [])]


def chat_json(messages, schema=None, settings=None):
	"""Send a chat request and return (parsed_json, raw_text).

	Uses response_format=json_schema when enabled (LocalAI turns it into a grammar so
	the model can only emit valid JSON). Falls back to an unconstrained request if the
	server rejects the schema, and always parses defensively.
	"""
	settings = settings or get_settings()
	payload = {
		"model": settings.vision_model,
		"messages": messages,
		"temperature": 0,
		"max_tokens": 4096,
	}
	if settings.disable_thinking:
		payload["reasoning_effort"] = "none"
	if schema and settings.use_json_schema:
		payload["response_format"] = {
			"type": "json_schema",
			"json_schema": {"name": "document", "strict": True, "schema": schema},
		}

	text = _post(settings, payload)
	if text is None and "response_format" in payload:
		payload.pop("response_format")
		text = _post(settings, payload)
	if text is None:
		raise LLMError(_("LocalAI returned an error, see Error Log"))
	return parse_json(text), text


def _post(settings, payload):
	try:
		r = requests.post(
			_url(settings, "/v1/chat/completions"),
			headers=_headers(settings),
			data=json.dumps(payload),
			timeout=settings.timeout or 300,
		)
	except requests.RequestException as e:
		raise LLMError(_("Cannot reach LocalAI at {0}: {1}").format(settings.base_url, e)) from e
	if r.status_code >= 400:
		frappe.log_error("NeotecAI Intake: LocalAI error", f"HTTP {r.status_code}\n{r.text[:4000]}")
		if r.status_code in (400, 422, 500):
			return None
		raise LLMError(_("LocalAI HTTP {0}: {1}").format(r.status_code, r.text[:300]))
	data = r.json()
	try:
		return data["choices"][0]["message"]["content"] or ""
	except (KeyError, IndexError) as e:
		raise LLMError(_("Unexpected LocalAI response: {0}").format(str(data)[:300])) from e


def parse_json(text):
	"""Extract the first JSON object from model output (handles <think>, code fences)."""
	text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
	text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
	try:
		return json.loads(text)
	except ValueError:
		pass
	start = text.find("{")
	if start == -1:
		raise LLMError(_("The AI did not return JSON"))
	depth, in_str, esc = 0, False, False
	for i in range(start, len(text)):
		ch = text[i]
		if in_str:
			if esc:
				esc = False
			elif ch == "\\":
				esc = True
			elif ch == '"':
				in_str = False
			continue
		if ch == '"':
			in_str = True
		elif ch == "{":
			depth += 1
		elif ch == "}":
			depth -= 1
			if depth == 0:
				return json.loads(text[start : i + 1])
	raise LLMError(_("The AI returned incomplete JSON"))
