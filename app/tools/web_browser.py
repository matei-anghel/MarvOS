"""Web browsing tool for Marvin using Playwright."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page, Browser


class WebBrowser:
    """Simple web browser automation using Playwright."""
    
    def __init__(self):
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._playwright = None
    
    async def start(self) -> dict[str, Any]:
        """Start the browser."""
        try:
            self._playwright = await async_playwright().start()
            # Avoid requiring `playwright install` by preferring an installed system browser.
            # On Windows, the Edge channel is typically available.
            launch_kwargs: dict[str, Any] = {"headless": False}
            if os.name == "nt":
                # Use system Edge if present; this does not require downloading Playwright-managed Chromium.
                launch_kwargs["channel"] = "msedge"
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._page = await self._browser.new_page()
            return {"ok": True, "status": "Browser started"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to a URL."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            await self._page.goto(url, wait_until="networkidle")
            title = await self._page.title()
            return {"ok": True, "url": url, "title": title}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def search(self, query: str, engine: str = "google") -> dict[str, Any]:
        """Perform a web search."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            if engine == "google":
                await self._page.goto(f"https://www.google.com/search?q={quote_plus(query)}", wait_until="networkidle")
            elif engine == "bing":
                await self._page.goto(f"https://www.bing.com/search?q={quote_plus(query)}", wait_until="networkidle")
            else:
                return {"ok": False, "error": f"Unknown search engine: {engine}"}
            
            title = await self._page.title()
            return {"ok": True, "query": query, "title": title}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def click(self, selector: str) -> dict[str, Any]:
        """Click on an element."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            await self._page.click(selector)
            return {"ok": True, "action": "click", "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def type(self, selector: str, text: str, submit: bool = False) -> dict[str, Any]:
        """Type text into an element."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            await self._page.fill(selector, text)
            if submit:
                await self._page.press(selector, "Enter")
            return {"ok": True, "action": "type", "selector": selector, "text": text}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def get_text(self, selector: str | None = None) -> dict[str, Any]:
        """Get text content from the page or a specific element."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            if selector:
                text = await self._page.text_content(selector)
            else:
                # Visible text, not raw HTML (markup and scripts would eat the 10k budget).
                text = await self._page.inner_text("body")
            return {"ok": True, "text": text[:10000] if text else ""}  # Limit text size
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def screenshot(self) -> dict[str, Any]:
        """Take a screenshot of the current page."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            screenshot_bytes = await self._page.screenshot()
            b64 = base64.b64encode(screenshot_bytes).decode("ascii")
            return {"ok": True, "screenshot_base64": b64}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def scroll(self, direction: str = "down", amount: int = 500) -> dict[str, Any]:
        """Scroll the page."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            if direction == "down":
                await self._page.evaluate(f"window.scrollBy(0, {amount})")
            elif direction == "up":
                await self._page.evaluate(f"window.scrollBy(0, -{amount})")
            return {"ok": True, "action": "scroll", "direction": direction}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def get_links(self) -> dict[str, Any]:
        """Get all links on the current page."""
        if not self._page:
            return {"ok": False, "error": "Browser not started"}
        
        try:
            links = await self._page.eval_on_selector_all("a", """
                elements => elements.map(el => ({
                    text: el.textContent?.trim() || "",
                    href: el.href || "",
                    visible: el.offsetParent !== null
                })).filter(l => l.visible && l.href)
            """)
            return {"ok": True, "links": links[:50]}  # Limit to 50 links
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def close(self) -> dict[str, Any]:
        """Close the browser."""
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            return {"ok": True, "status": "Browser closed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# Global browser instance (per session)
_browser_instances: dict[str, WebBrowser] = {}


async def web_browse(
    action: str,
    session_id: str,
    url: str | None = None,
    query: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    submit: bool = False,
    engine: str = "google",
    direction: str = "down",
) -> dict[str, Any]:
    """
    Execute a web browsing action.
    
    Actions:
    - start: Start the browser
    - navigate: Navigate to a URL
    - search: Perform a web search
    - click: Click on an element
    - type: Type text into an element
    - get_text: Get page text content
    - screenshot: Take a screenshot
    - scroll: Scroll the page
    - get_links: Get all links on the page
    - close: Close the browser
    """
    browser_key = f"browser_{session_id}"
    
    if action == "start":
        if browser_key in _browser_instances:
            return {"ok": True, "status": "Browser already running"}
        browser = WebBrowser()
        result = await browser.start()
        # Only cache a browser that actually started, so a failed launch can be retried.
        if result["ok"]:
            _browser_instances[browser_key] = browser
        else:
            await browser.close()
        return result
    
    if browser_key not in _browser_instances:
        # Auto-start browser if not running
        browser = WebBrowser()
        start_result = await browser.start()
        if not start_result["ok"]:
            await browser.close()
            return start_result
        _browser_instances[browser_key] = browser
    
    browser = _browser_instances[browser_key]
    
    if action == "navigate":
        return await browser.navigate(url) if url else {"ok": False, "error": "URL required"}
    
    elif action == "search":
        return await browser.search(query, engine) if query else {"ok": False, "error": "Query required"}
    
    elif action == "click":
        return await browser.click(selector) if selector else {"ok": False, "error": "Selector required"}
    
    elif action == "type":
        if not selector or text is None:
            return {"ok": False, "error": "Selector and text required"}
        return await browser.type(selector, text, submit)
    
    elif action == "get_text":
        return await browser.get_text(selector)
    
    elif action == "screenshot":
        return await browser.screenshot()
    
    elif action == "scroll":
        return await browser.scroll(direction)
    
    elif action == "get_links":
        return await browser.get_links()
    
    elif action == "close":
        result = await browser.close()
        del _browser_instances[browser_key]
        return result
    
    else:
        return {"ok": False, "error": f"Unknown action: {action}"}
