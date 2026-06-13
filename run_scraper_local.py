import sys
sys.path.append('functions')
import swehockey_stats_scraper

class MockRequest:
    pass

swehockey_stats_scraper.run_swehockey_stats_scraper(MockRequest())
