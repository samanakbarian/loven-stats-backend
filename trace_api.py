import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        api_requests = []
        def handle_request(request):
            if 'api' in request.url.lower() or 'graphql' in request.url.lower() or 's8y.se' in request.url.lower():
                if request.resource_type in ['fetch', 'xhr', 'document']:
                    api_requests.append(request.url)
        
        page.on("request", handle_request)
        
        try:
            await page.goto("https://www.hockeyallsvenskan.se/statistik/spelare", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(5000) # Wait 5s for API requests
        except Exception as e:
            print("Error:", e)
        
        print("Found API Requests:")
        for url in set(api_requests):
            print(url)
            
        await browser.close()

asyncio.run(main())
