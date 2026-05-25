"""
AI Engine — target-aware messaging and form mapping.
Primary: Ollama. Fallback: OpenAI-compatible REST.
"""

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)


def _get_ai_config():
    provider = os.getenv("AI_PROVIDER", "ollama")
    api_key = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    model = os.getenv("AI_MODEL", "llama3.1:8b")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_api_key = os.getenv("OLLAMA_API_KEY", "")
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
    }


def _call_ollama_sdk(system_prompt, user_prompt, temperature=0.3, max_tokens=1024):
    try:
        import ollama

        config = _get_ai_config()
        headers = None
        if config.get("ollama_api_key"):
            headers = {"Authorization": f"Bearer {config['ollama_api_key']}"}
        client = ollama.Client(host=config["ollama_host"], headers=headers)
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
        # If Ollama is selected and SDK failed, do not fall back to REST by default
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
- value: string/boolean
- action: fill | select | check | skip
"""

    user = f"""Company Data:
{json.dumps(company_data, indent=2)}

Target Summary:
{target_summary}

Form Fields:
{chr(10).join(field_descriptions)}

Return ONLY the JSON mapping."""

    response = _call_llm(system, user, temperature=0.1, max_tokens=2048)
    if response:
        try:
            mapping = json.loads(_extract_json(response))
            for k, v in mapping.items():
                if not (isinstance(v, dict) and "value" in v and "action" in v):
                    raise ValueError(f"Invalid mapping for key {k}")
            return mapping
        except Exception:
            pass

    return _map_fields_rules(fields, company_data)


def ai_summarize_target(target_url, page_snippet, company_data):
    system = """You are an institutional outreach analyst. Summarize the target company's
business focus, products, and how Orbital Industries should frame a partnership.
Return JSON: {"summary": "...", "angle": "...", "suggested_message": "..."}.
"""

    user = f"""Target URL: {target_url}

Target page snippet:
{page_snippet[:4000]}

Our company:
- {company_data['company']} ({company_data['company_url']})
- Focus: {company_data['industry']}
- Bio: {company_data['bio']}

Return ONLY JSON."""

    response = _call_llm(system, user, temperature=0.4, max_tokens=800)
    if response:
        try:
            return json.loads(_extract_json(response))
        except Exception:
            pass

    return {
        "summary": "Target summary unavailable",
        "angle": "General institutional partnership exploration",
        "suggested_message": company_data["message"],
    }


def ai_generate_additional_message(company_data, target_summary):
    system = """Write a concise, professional message for a contact form's
additional information box. It must reference the target's business and
why Orbital Industries is relevant. Keep it under 800 characters."""

    user = f"""Target summary:
{json.dumps(target_summary, indent=2)}

Our company data:
{json.dumps(company_data, indent=2)}

Return ONLY the message string."""

    response = _call_llm(system, user, temperature=0.5, max_tokens=400)
    if response:
        return response.strip()
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
