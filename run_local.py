import sys
from unittest import mock
sys.path.append('functions')
from swehockey_stats_scraper import run_swehockey_stats_scraper

req = mock.Mock()
print("Running scraper locally...")
res = run_swehockey_stats_scraper(req)
print(res)
