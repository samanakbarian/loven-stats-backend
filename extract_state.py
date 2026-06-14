import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto("https://www.hockeyallsvenskan.se/statistik/spelare", wait_until="domcontentloaded", timeout=30000)
        
        # Evaluate to get the initial state
        state = await page.evaluate("() => window.__INITIAL_STATE__")
        
        with open('initial_state.json', 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            
        print("Saved state to initial_state.json")
        await browser.close()

asyncio.run(main())
