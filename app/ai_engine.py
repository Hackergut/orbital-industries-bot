"""AI Engine — target-aware messaging and form mapping.
Primary: Ollama REST (no SDK). Fallback: OpenAI-compatible REST.
Added: Redis caching for LLM calls to reduce latency and cost.
"""
import json
import logging
import os

import requests

from app.cache import cache

logger = logging.getLogger(__name__)


def _get_ai_config():
    provider = os.getenv("AI_PROVIDER", "ollama")
    api_key = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    model = os.getenv("AI_MODEL", "llama3.1:8b")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_api_key = os.getenv("OLLAMA_API_KEY", "")
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "15"))
    # Fallback Ollama endpoint
    ollama_host_2 = os.getenv("OLLAMA_HOST_2", "")
    ollama_api_key_2 = os.getenv("OLLAMA_API_KEY_2", "")
    base_url = os.getenv("AI_BASE_URL", "")
    if provider == "ollama" and not base_url:
        base_url = ollama_host  # raw host, e.g. http://localhost:11434
    return {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
        "ollama_host": ollama_host,
        "ollama_api_key": ollama_api_key,
        "ollama_timeout": ollama_timeout,
        "ollama_host_2": ollama_host_2,
        "ollama_api_key_2": ollama_api_key_2,
    }


def _call_ollama_rest(system_prompt, user_prompt, temperature=0.3, max_tokens=1024, host=None, api_key=None):
    """Call Ollama via plain REST (no SDK). Supports fallback host/api_key."""
    config = _get_ai_config()
    use_host = host or config['base_url']
    url = f"{use_host}/api/chat"
    headers = {"Content-Type": "application/json"}
    auth_key = api_key or config.get("ollama_api_key")
    if auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=config["ollama_timeout"])
        r.raise_for_status()
        data = r.json()
        return data.get("message", {}).get("content", "")
    except requests.exceptions.Timeout:
        logger.warning("Ollama REST timed out (%ss) on %s", config["ollama_timeout"], use_host)
    except Exception:
        logger.warning("Ollama REST call failed on %s", use_host)
    return None


def _call_openai_rest(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    """Call any OpenAI-compatible endpoint (e.g. Groq, Together, OpenRouter)."""
    config = _get_ai_config()
    if not config["api_key"]:
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
    }

    payload = {
        "model": config["model"],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    base = config["base_url"] or "https://api.openai.com/v1"
    url = f"{base}/chat/completions"

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("OpenAI-compatible REST call failed")
        return None


def _call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    # Check cache first
    cache_payload = {
        "system_prompt_hash": hash(system_prompt) & 0xFFFFFFFF,
        "user_prompt_hash": hash(user_prompt) & 0xFFFFFFFF,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "model": _get_ai_config()["model"],
    }
    cached = cache.get("llm_call", cache_payload)
    if cached is not None:
        logger.info("LLM cache hit")
        return cached

    config = _get_ai_config()
    result = None
    if config["provider"] == "ollama":
        # Try primary Ollama endpoint
        result = _call_ollama_rest(system_prompt, user_prompt, temperature, max_tokens)
        if result:
            return result
        # Try fallback Ollama endpoint (host_2) if configured
        if config.get("ollama_host_2"):
            logger.info("Trying fallback Ollama endpoint: %s", config["ollama_host_2"])
            result = _call_ollama_rest(
                system_prompt, user_prompt, temperature, max_tokens,
                host=config["ollama_host_2"],
                api_key=config.get("ollama_api_key_2")
            )
            if result:
                return result
        # Fallback to OpenAI-compatible if configured
        if config["api_key"] and config.get("base_url") and config["base_url"] != config["ollama_host"]:
            result = _call_openai_rest(system_prompt, user_prompt, temperature, max_tokens)
            if result:
                return result
        return None

    result = _call_openai_rest(system_prompt, user_prompt, temperature, max_tokens)
    if result:
        cache.set("llm_call", cache_payload, result, ttl=3600)
        return result
    return None


def _extract_json(text):
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return text


def ai_map_fields_smart(fields, company_data, target_summary):
    if os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true":
        mapping = _map_fields_rules(fields, company_data)
        for idx, f in enumerate(fields):
            k = str(idx)
            if f.get("required") and mapping.get(k, {}).get("action") == "skip":
                mapping[k] = _required_fallback_value(f, company_data)
            if k in mapping and f.get("frame_index") is not None:
                mapping[k]["frame_index"] = f.get("frame_index")
        return mapping

    cache_payload = {
        "fields": [
            {
                "tag": f.get("tag"),
                "name": f.get("name"),
                "id": f.get("id"),
                "placeholder": f.get("placeholder"),
                "label": f.get("label_text"),
                "type": f.get("type"),
                "required": f.get("required"),
            }
            for f in fields
        ],
        "company_keys": sorted(company_data.keys()),
        "target_angle": (target_summary or {}).get("angle", "") if target_summary else "",
    }
    cached = cache.get("field_mapping", cache_payload)
    if cached:
        logger.info("Field mapping cache hit")
        return cached

    system = """You are an intelligent form-filling assistant.
Map each field index to the correct company data value.
Return ONLY JSON in this exact format:
{"0":{"value":"...","action":"fill"},"1":{"value":"...","action":"check"}}
Use action: fill, check, select, or skip."""

    user = f"""Fields detected on the page:\n{json.dumps(fields, indent=2)}\n\nCompany data:\n{json.dumps(company_data, indent=2)}\n\nTarget context:\n{json.dumps(target_summary or {}, indent=2)}\n\nReturn ONLY JSON mapping."""

    response = _call_llm(system, user, temperature=0.2, max_tokens=1024)
    if response:
        try:
            mapping = json.loads(_extract_json(response))
            for idx, f in enumerate(fields):
                k = str(idx)
                if f.get("required") and mapping.get(k, {}).get("action") == "skip":
                    mapping[k] = _required_fallback_value(f, company_data)
                if k in mapping and f.get("frame_index") is not None:
                    mapping[k]["frame_index"] = f.get("frame_index")
            cache.set("field_mapping", cache_payload, mapping, ttl=3600)
            return mapping
        except Exception:
            logger.exception("Failed to parse AI field mapping")

    mapping = _map_fields_rules(fields, company_data)
    for idx, f in enumerate(fields):
        k = str(idx)
        if f.get("required") and mapping.get(k, {}).get("action") == "skip":
            mapping[k] = _required_fallback_value(f, company_data)
        if k in mapping and f.get("frame_index") is not None:
            mapping[k]["frame_index"] = f.get("frame_index")
    return mapping


def _required_fallback_value(field, company_data):
    raw = " ".join([field.get("name", ""), field.get("id", ""), field.get("placeholder", "")]).lower()
    if "email" in raw:
        return {"value": company_data["email"], "action": "fill"}
    if "phone" in raw or "tel" in raw:
        return {"value": company_data["phone"], "action": "fill"}
    if "company" in raw or "org" in raw:
        return {"value": company_data["company"], "action": "fill"}
    if "name" in raw:
        return {"value": company_data["full_name"], "action": "fill"}
    if "message" in raw or "comment" in raw or "note" in raw:
        return {"value": company_data["message"], "action": "fill"}
    return {"value": "N/A", "action": "fill"}


def ai_summarize_target(target_url, page_snippet, company_data):
    cache_payload = {"url": target_url}
    cached = cache.get("target_summary", cache_payload)
    if cached:
        logger.info("Target summary cache hit for %s", target_url)
        return cached

    system = """You are an institutional outreach analyst. Summarize the target company's
business focus, products, and how Orbital Industries should frame a partnership.
Return JSON: {"summary": "...", "angle": "...", "suggested_message": "..."}."""

    user = f"""Target URL: {target_url}\n\nTarget page snippet:\n{page_snippet[:4000]}\n\nOur company:\n- {company_data['company']} ({company_data['company_url']})\n- Focus: {company_data['industry']}\n- Bio: {company_data['bio']}\n\nReturn ONLY JSON."""

    response = _call_llm(system, user, temperature=0.4, max_tokens=800)
    if response:
        try:
            result = json.loads(_extract_json(response))
            cache.set("target_summary", cache_payload, result, ttl=3600)
            return result
        except Exception:
            logger.exception("Failed to parse target summary JSON")

    return {
        "summary": "Target summary unavailable",
        "angle": "General institutional partnership exploration",
        "suggested_message": company_data["message"],
    }


def ai_generate_additional_message(company_data, target_summary):
    cache_payload = {
        "company": company_data.get("company", ""),
        "angle": target_summary.get("angle", "") if target_summary else "",
    }
    cached = cache.get("additional_message", cache_payload)
    if cached:
        logger.info("Additional message cache hit")
        return cached

    system = """Write a concise, professional message for a contact form's
additional information box. It must reference the target's business and
why Orbital Industries is relevant. Keep it under 800 characters."""

    user = f"""Target summary:\n{json.dumps(target_summary, indent=2)}\n\nOur company data:\n{json.dumps(company_data, indent=2)}\n\nReturn ONLY the message string."""

    response = _call_llm(system, user, temperature=0.5, max_tokens=400)
    if response:
        result = response.strip()
        cache.set("additional_message", cache_payload, result, ttl=3600)
        return result
    return company_data["message"]


def _map_fields_rules(fields, company_data):
    mapping = {}
    for idx, f in enumerate(fields):
        raw = " ".join(
            [f.get("name", ""), f.get("id", ""), f.get("placeholder", ""),
             f.get("label_text", ""), f.get("aria_label", "")]
        ).lower()
        k = str(idx)
        ftype = (f.get("type") or "").lower()

        if ftype in {"checkbox", "radio"}:
            consent_tokens = ["consent", "privacy", "policy", "agree", "terms", "gdpr"]
            if f.get("required") or any(t in raw for t in consent_tokens):
                mapping[k] = {"value": True, "action": "check"}
            else:
                mapping[k] = {"value": "", "action": "skip"}
            continue

        if any(x in raw for x in ["first", "fname", "first_name", "firstname"]):
            mapping[k] = {"value": company_data["first_name"], "action": "fill"}
        elif any(x in raw for x in ["last", "lname", "last_name", "surname", "lastname"]):
            mapping[k] = {"value": company_data["last_name"], "action": "fill"}
        elif any(x in raw for x in ["full", "full_name"]):
            mapping[k] = {"value": company_data["full_name"], "action": "fill"}
        elif any(x in raw for x in ["email", "e-mail", "mail"]):
            mapping[k] = {"value": company_data["email"], "action": "fill"}
        elif any(x in raw for x in ["phone", "tel", "telephone", "mobile"]):
            mapping[k] = {"value": company_data["phone"], "action": "fill"}
        elif any(x in raw for x in ["company", "organization", "org", "firm"]):
            mapping[k] = {"value": company_data["company"], "action": "fill"}
        elif any(x in raw for x in ["website", "url", "site"]):
            mapping[k] = {"value": company_data["company_url"], "action": "fill"}
        elif any(x in raw for x in ["job", "title", "position", "role"]):
            mapping[k] = {"value": company_data["job_title"], "action": "fill"}
        elif any(x in raw for x in ["industry", "sector", "field"]):
            mapping[k] = {"value": company_data["industry"], "action": "fill"}
        elif any(x in raw for x in ["message", "comment", "note", "description", "additional", "about", "details"]):
            mapping[k] = {"value": company_data["message"], "action": "fill"}
        elif any(x in raw for x in ["country"]):
            mapping[k] = {"value": company_data["country"], "action": "fill"}
        elif any(x in raw for x in ["city"]):
            mapping[k] = {"value": company_data["city"], "action": "fill"}
        elif any(x in raw for x in ["employee", "size", "staff", "team", "people"]):
            mapping[k] = {"value": company_data["employees"], "action": "fill"}
        elif f.get("tag") == "select":
            mapping[k] = {"value": company_data["country"], "action": "select"}
        else:
            mapping[k] = {"value": "", "action": "skip"}

    return mapping
