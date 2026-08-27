"""Shared constants for scrapers: headers, endpoints, UA strings."""

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# --- LinkedIn ---
LINKEDIN_BASE_URL = "https://www.linkedin.com"
LINKEDIN_SEARCH_URL = (
    f"{LINKEDIN_BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
LINKEDIN_JOB_URL = f"{LINKEDIN_BASE_URL}/jobs/view"

LINKEDIN_HEADERS = {
    "authority": "www.linkedin.com",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": CHROME_UA,
}

# --- Indeed ---
INDEED_API_URL = "https://apis.indeed.com/graphql"

INDEED_HEADERS = {
    "Host": "apis.indeed.com",
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "accept": "application/json",
    "indeed-locale": "en-US",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Indeed App 193.1"
    ),
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
}

INDEED_SEARCH_QUERY = """
    query GetJobData {{
        jobSearch(
        {what}
        {location}
        limit: 100
        {cursor}
        sort: RELEVANCE
        {filters}
        ) {{
        pageInfo {{
            nextCursor
        }}
        results {{
            job {{
            key
            title
            datePublished
            description {{
                html
            }}
            location {{
                countryCode
                admin1Code
                city
                formatted {{
                    short
                    long
                }}
            }}
            compensation {{
                estimated {{
                    currencyCode
                    baseSalary {{
                        unitOfWork
                        range {{
                        ... on Range {{
                            min
                            max
                        }}
                        }}
                    }}
                }}
                baseSalary {{
                    unitOfWork
                    range {{
                    ... on Range {{
                        min
                        max
                    }}
                    }}
                }}
                currencyCode
            }}
            attributes {{
                key
                label
            }}
            employer {{
                name
            }}
            }}
        }}
        }}
    }}
"""

# --- Jora (AU) ---
JORA_BASE_URL = "https://au.jora.com"
# /jobs ignores q/l (always returns all jobs); /j is the search endpoint.
JORA_SEARCH_URL = f"{JORA_BASE_URL}/j"

JORA_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "referer": JORA_BASE_URL,
    "user-agent": CHROME_UA,
}
