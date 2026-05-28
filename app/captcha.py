"""CAPTCHA bypass via 2Captcha (reCAPTCHA v2/v3, hCaptcha)."""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")
TWOCAPTCHA_BASE = "https://2captcha.com"


def _apply_stealth(page):
    page.evaluate("""() => {
        try {
            const wd = Object.getOwnPropertyDescriptor(navigator, 'webdriver');
            if (!wd || wd.configurable) {
                Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
            }
        } catch (e) {}
        try {
            const pl = Object.getOwnPropertyDescriptor(navigator, 'plugins');
            if (!pl || pl.configurable) {
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], configurable: true });
            }
        } catch (e) {}
        try {
            const lg = Object.getOwnPropertyDescriptor(navigator, 'languages');
            if (!lg || lg.configurable) {
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
            }
        } catch (e) {}
        try { window.chrome = { runtime: {} }; } catch (e) {}
        try {
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        } catch (e) {}
        try { delete window.__playwright; } catch (e) {}
        try { delete window.__pw_manual; } catch (e) {}
    }""")


def solve_recaptcha_v2(page, site_key, page_url):
    if not TWOCAPTCHA_API_KEY:
        return None

    r = requests.post(f"{TWOCAPTCHA_BASE}/in.php", data={
        "key": TWOCAPTCHA_API_KEY,
        "method": "userrecaptcha",
        "googlekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }, timeout=30)
    result = r.json()
    if result.get("status") != 1:
        logger.error("2Captcha submit failed: %s", result.get("request"))
        return None

    request_id = result["request"]
    for _ in range(60):
        time.sleep(5)
        r = requests.get(f"{TWOCAPTCHA_BASE}/res.php", params={
            "key": TWOCAPTCHA_API_KEY,
            "action": "get",
            "id": request_id,
            "json": 1,
        }, timeout=30)
        result = r.json()
        if result.get("status") == 1:
            return result["request"]
        if result.get("request") != "CAPCHA_NOT_READY":
            return None

    return None


def solve_recaptcha_v3(page, site_key, page_url, action="submit"):
    if not TWOCAPTCHA_API_KEY:
        return None

    r = requests.post(f"{TWOCAPTCHA_BASE}/in.php", data={
        "key": TWOCAPTCHA_API_KEY,
        "method": "userrecaptcha",
        "version": "v3",
        "googlekey": site_key,
        "pageurl": page_url,
        "action": action,
        "min_score": 0.3,
        "json": 1,
    }, timeout=30)
    result = r.json()
    if result.get("status") != 1:
        return None

    request_id = result["request"]
    for _ in range(60):
        time.sleep(5)
        r = requests.get(f"{TWOCAPTCHA_BASE}/res.php", params={
            "key": TWOCAPTCHA_API_KEY,
            "action": "get",
            "id": request_id,
            "json": 1,
        }, timeout=30)
        result = r.json()
        if result.get("status") == 1:
            return result["request"]
        if result.get("request") != "CAPCHA_NOT_READY":
            return None

    return None


def solve_hcaptcha(page, site_key, page_url):
    if not TWOCAPTCHA_API_KEY:
        return None

    r = requests.post(f"{TWOCAPTCHA_BASE}/in.php", data={
        "key": TWOCAPTCHA_API_KEY,
        "method": "hcaptcha",
        "sitekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }, timeout=30)
    result = r.json()
    if result.get("status") != 1:
        return None

    request_id = result["request"]
    for _ in range(60):
        time.sleep(5)
        r = requests.get(f"{TWOCAPTCHA_BASE}/res.php", params={
            "key": TWOCAPTCHA_API_KEY,
            "action": "get",
            "id": request_id,
            "json": 1,
        }, timeout=30)
        result = r.json()
        if result.get("status") == 1:
            return result["request"]
        if result.get("request") != "CAPCHA_NOT_READY":
            return None

    return None


def solve_captcha_if_present(session_id, browser_mgr):
    return browser_mgr.solve_captcha(session_id)


def _solve_captcha_inner(page):
    _apply_stealth(page)
    page_url = page.url

    recaptcha_v2 = page.evaluate("""() => {
        const el = document.querySelector('.g-recaptcha');
        if (el) return { type: 'recaptcha_v2', sitekey: el.getAttribute('data-sitekey') };
        const iframe = document.querySelector('iframe[src*="recaptcha"]');
        if (iframe) {
            const src = iframe.getAttribute('src');
            const match = src.match(/[?&]k=([^&]+)/);
            if (match) return { type: 'recaptcha_v2', sitekey: match[1] };
        }
        return null;
    }""")

    if recaptcha_v2 and recaptcha_v2.get("sitekey"):
        token = solve_recaptcha_v2(page, recaptcha_v2["sitekey"], page_url)
        if token:
            page.evaluate("""(token) => {
                const el = document.getElementById('g-recaptcha-response');
                if (el) el.innerHTML = token;
                try { ___grecaptcha_cfg.clients[0].callback(token); } catch (e) {}
            }""", token)
            try:
                page.fill("#g-recaptcha-response", token, timeout=3000)
            except Exception:
                pass
            return True

    recaptcha_v3 = page.evaluate("""() => {
        const scripts = document.querySelectorAll('script[src*="recaptcha"]');
        for (const s of scripts) {
            if (s.src.includes('render=')) {
                const match = s.src.match(/render=([^&]+)/);
                if (match && match[1] !== 'explicit') return { type: 'recaptcha_v3', sitekey: match[1] };
            }
        }
        return null;
    }""")

    if recaptcha_v3 and recaptcha_v3.get("sitekey"):
        token = solve_recaptcha_v3(page, recaptcha_v3["sitekey"], page_url)
        if token:
            page.evaluate("""(token) => {
                const input = document.querySelector('input[name*=\"recaptcha\"]');
                if (input) input.value = token;
            }""", token)
            return True

    hcaptcha = page.evaluate("""() => {
        const el = document.querySelector('.h-captcha');
        if (el) return { type: 'hcaptcha', sitekey: el.getAttribute('data-sitekey') };
        const iframe = document.querySelector('iframe[src*="hcaptcha"]');
        if (iframe) {
            const src = iframe.getAttribute('src');
            const match = src.match(/sitekey=([^&]+)/);
            if (match) return { type: 'hcaptcha', sitekey: match[1] };
        }
        return null;
    }""")

    if hcaptcha and hcaptcha.get("sitekey"):
        token = solve_hcaptcha(page, hcaptcha["sitekey"], page_url)
        if token:
            page.evaluate("""(token) => {
                const el = document.querySelector('[name="h-captcha-response"]');
                if (el) el.value = token;
                const ta = document.querySelector('textarea[name="h-captcha-response"]');
                if (ta) ta.value = token;
            }""", token)
            return True

    return False

def _solve_captcha_inner_selenium(driver):
    """CAPTCHA bypass using Selenium WebDriver."""
    _apply_stealth_selenium(driver)
    page_url = driver.current_url

    recaptcha_v2 = driver.execute_script("""
        const el = document.querySelector('.g-recaptcha');
        if (el) return { type: 'recaptcha_v2', sitekey: el.getAttribute('data-sitekey') };
        const iframe = document.querySelector('iframe[src*="recaptcha"]');
        if (iframe) {
            const src = iframe.getAttribute('src');
            const match = src.match(/[?&]k=([^&]+)/);
            if (match) return { type: 'recaptcha_v2', sitekey: match[1] };
        }
        return null;
    """)

    if recaptcha_v2 and recaptcha_v2.get('sitekey'):
        token = solve_recaptcha_v2(driver, recaptcha_v2['sitekey'], page_url)
        if token:
            driver.execute_script("""
                const el = document.getElementById('g-recaptcha-response');
                if (el) el.innerHTML = arguments[0];
                try { ___grecaptcha_cfg.clients[0].callback(arguments[0]); } catch (e) {}
            """, token)
            try:
                from selenium.webdriver.common.by import By
                driver.find_element(By.CSS_SELECTOR, '#g-recaptcha-response').send_keys(token)
            except Exception:
                pass
            return True

    recaptcha_v3 = driver.execute_script("""
        const scripts = document.querySelectorAll('script[src*="recaptcha"]');
        for (const s of scripts) {
            if (s.src.includes('render=')) {
                const match = s.src.match(/render=([^&]+)/);
                if (match && match[1] !== 'explicit') return { type: 'recaptcha_v3', sitekey: match[1] };
            }
        }
        return null;
    """)

    if recaptcha_v3 and recaptcha_v3.get('sitekey'):
        token = solve_recaptcha_v3(driver, recaptcha_v3['sitekey'], page_url)
        if token:
            driver.execute_script("""
                const input = document.querySelector('input[name*="recaptcha"]');
                if (input) input.value = arguments[0];
            """, token)
            return True

    hcaptcha = driver.execute_script("""
        const el = document.querySelector('.h-captcha');
        if (el) return { type: 'hcaptcha', sitekey: el.getAttribute('data-sitekey') };
        const iframe = document.querySelector('iframe[src*="hcaptcha"]');
        if (iframe) {
            const src = iframe.getAttribute('src');
            const match = src.match(/sitekey=([^&]+)/);
            if (match) return { type: 'hcaptcha', sitekey: match[1] };
        }
        return null;
    """)

    if hcaptcha and hcaptcha.get('sitekey'):
        token = solve_hcaptcha(driver, hcaptcha['sitekey'], page_url)
        if token:
            driver.execute_script("""
                const el = document.querySelector('[name="h-captcha-response"]');
                if (el) el.value = arguments[0];
                const ta = document.querySelector('textarea[name="h-captcha-response"]');
                if (ta) ta.value = arguments[0];
            """, token)
            return True

    return False


def _apply_stealth_selenium(driver):
    driver.execute_script("""
        try {
            const wd = Object.getOwnPropertyDescriptor(navigator, 'webdriver');
            if (!wd || wd.configurable) {
                Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
            }
        } catch (e) {}
        try {
            const pl = Object.getOwnPropertyDescriptor(navigator, 'plugins');
            if (!pl || pl.configurable) {
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], configurable: true });
            }
        } catch (e) {}
        try {
            const lg = Object.getOwnPropertyDescriptor(navigator, 'languages');
            if (!lg || lg.configurable) {
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
            }
        } catch (e) {}
        try { window.chrome = { runtime: {} }; } catch (e) {}
        try {
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        } catch (e) {}
        try { delete window.__playwright; } catch (e) {}
        try { delete window.__pw_manual; } catch (e) {}
    """)
