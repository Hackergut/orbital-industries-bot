"""
AI Engine — target-aware messaging and form mapping.
Primary: Ollama. Fallback: OpenAI-compatible REST.
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
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "60"))
    base_url = os.getenv("AI_BASE_URL", "")
    if provider == "ollama" and not base_url:
        base_url = f"{ollama_host}/v1"
    return {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
        "ollama_host": ollama_host,
        "ollama_api_key": ollama_api_key,
        "ollama_timeout": ollama_timeout,
    }


def _call_ollama_sdk(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    try:
        import ollama

        config = _get_ai_config()
        headers = None
        if config.get("ollama_api_key"):
            headers = {"Authorization": f"Bearer {config['ollama_api_key']}"}
        client = ollama.Client(
            host=config["ollama_host"],
            headers=headers,
            timeout=config.get("ollama_timeout", 60),
        )
        response = client.chat(
            model=config["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": temperature, "num_predict": max_tokens},
        )
        return response["message"]["content"]
    except ImportError:
        logger.warning("ollama-python not installed, falling back to REST")
        return None
    except Exception as e:
        logger.error("Ollama SDK call failed: %s", e)
        return None


def _call_llm_rest(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    config = _get_ai_config()
    if config["provider"] == "ollama":
        return None
    if not config["api_key"] and config["provider"] != "ollama":
        return None

    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"

    payload = {
        "model": config["model"],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    url = f"{config['base_url']}/chat/completions" if config["base_url"] else "https://api.openai.com/v1/chat/completions"

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("LLM REST call failed")
        return None


def _call_llm(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    config = _get_ai_config()
    if config["provider"] == "ollama":
        result = _call_ollama_sdk(system_prompt, user_prompt, temperature, max_tokens)
        if result:
            return result
        return None

    result = _call_llm_rest(system_prompt, user_prompt, temperature, max_tokens)
    if result:
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
    # If LLM is disabled, go straight to rules
    if os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true":
        mapping = _map_fields_rules(fields, company_data)
        for idx, f in enumerate(fields):
            k = str(idx)
            if f.get("required") and mapping.get(k, {}).get("action") == "skip":
                mapping[k] = _required_fallback_value(f, company_data)
            if k in mapping and f.get("frame_index") is not None:
                mapping[k]["frame_index"] = f.get("frame_index")
        return mapping

    # Cache key: hash of field descriptions + company data keys + target summary
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
        "summary": target_summary.get("summary", "") if target_summary else "",
    }
    cached = cache.get("field_map", cache_payload)
    if cached:
        logger.info("Field mapping cache hit")
        for idx, f in enumerate(fields):
            k = str(idx)
            if k in cached and f.get("frame_index") is not None:
                cached[k]["frame_index"] = f.get("frame_index")
        return cached

    field_descriptions = []
    for i, f in enumerate(fields):
        desc = (
            f"Field {i}: tag={f.get('tag')}, name={f.get('name')}, id={f.get('id')}, "
            f"placeholder={f.get('placeholder')}, label={f.get('label_text')}, type={f.get('type')}"
        )
        field_descriptions.append(desc)

    system = """You are a form field mapper for Orbital Industries Limited. Given form fields,
company data, and a target summary, map each field to the most accurate value.
Return JSON: keys are field indices (as strings). Values are objects with:
- "value": the string/boolean to fill
- "action": one of "fill", "check", "select", "skip"
Be concise."""

    fields_str = "\n".join(field_descriptions)
    user = f"""Fields:
{fields_str}

Company data:
{json.dumps(company_data, indent=2)}

Target summary:
{json.dumps(target_summary, indent=2) if target_summary else "N/A"}

Return ONLY JSON."""

    response = _call_llm(system, user, temperature=0.3, max_tokens=1024)
    if response:
        try:
            parsed = json.loads(_extract_json(response))
            # Validate structure
            for k, v in parsed.items():
                if isinstance(v, dict) and "action" in v:
                    pass
                else:
                    parsed[k] = {"value": str(v), "action": "fill"}
            cache.set("field_map", cache_payload, parsed, ttl=7200)
            for idx, f in enumerate(fields):
                k = str(idx)
                if k in parsed and f.get("frame_index") is not None:
                    parsed[k]["frame_index"] = f.get("frame_index")
            return parsed
        except Exception as e:
            logger.warning("LLM field mapping parse failed: %s", e)

    # Fallback to rules
    mapping = _map_fields_rules(fields, company_data)
    for idx, f in enumerate(fields):
        k = str(idx)
        if f.get("required") and mapping.get(k, {}).get("action") == "skip":
            mapping[k] = _required_fallback_value(f, company_data)
        if k in mapping and f.get("frame_index") is not None:
            mapping[k]["frame_index"] = f.get("frame_index")
    return mapping


def _required_fallback_value(field, company_data):
    tag = field.get("tag")
    ftype = (field.get("type") or "").lower()
    if ftype == "email":
        return {"value": company_data["email"], "action": "fill"}
    if ftype in {"tel", "phone"}:
        return {"value": company_data["phone"], "action": "fill"}
    if tag == "textarea":
        return {"value": company_data["message"], "action": "fill"}
    return {"value": company_data["full_name"], "action": "fill"}


def ai_summarize_target(target_url, page_snippet, company_data):
    cache_payload = {
        "url": target_url,
        "snippet_hash": hash(page_snippet[:500]),
        "company": company_data.get("company", ""),
    }
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
            pass

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
