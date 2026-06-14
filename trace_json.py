import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        json_requests = []
        async def handle_response(response):
            if 'application/json' in response.headers.get('content-type', '').lower():
                json_requests.append(response.url)
        
        page.on("response", handle_response)
        
        try:
            await page.goto("https://www.hockeyallsvenskan.se/statistik/spelare", wait_until="networkidle", timeout=15000)
        except Exception as e:
            pass
        
        print("Found JSON Responses:")
        for url in set(json_requests):
            print(url)
            
        await browser.close()

asyncio.run(main())
