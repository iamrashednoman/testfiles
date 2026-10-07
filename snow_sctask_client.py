import json
import requests
from requests.auth import HTTPBasicAuth

# ==========================================
# CONFIGURATION
# ==========================================
SNOW_INSTANCE = "your_instance_name"  # e.g., "dev12345"
SNOW_USERNAME = "your_api_username"
SNOW_PASSWORD = "your_api_password"

# Querying 'task' returns all ticket types (Incidents, SCTASKs, Changes, etc.)
TABLE_NAME = "task"
BASE_URL = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/{TABLE_NAME}"

AUTH = HTTPBasicAuth(SNOW_USERNAME, SNOW_PASSWORD)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Key fields common across all ServiceNow tasks
FIELDS = (
    "sys_id,number,sys_class_name,short_description,state,priority,"
    "assignment_group,assigned_to,opened_by,sys_created_on"
)


# ==========================================
# FETCH ALL TICKETS (WITH PAGINATION)
# ==========================================
def fetch_all_tickets(query_filter="active=true", page_size=100, max_records=None):
    """
    Fetches tickets in batches using sysparm_offset pagination.
    
    :param query_filter: Encoded query string (e.g., 'active=true^ORDERBYDESCsys_created_on')
    :param page_size: Number of records to retrieve per API call (default 100)
    :param max_records: Limit the total records fetched, or None for all
    :return: List of ticket dictionaries
    """
    all_tickets = []
    offset = 0

    print(f"Connecting to {SNOW_INSTANCE}.service-now.com...")
    print(f"Query: {query_filter}\n")

    while True:
        params = {
            "sysparm_query": query_filter,
            "sysparm_fields": FIELDS,
            "sysparm_display_value": "all",  # Returns both .value and .display_value
            "sysparm_limit": page_size,
            "sysparm_offset": offset
        }

        try:
            response = requests.get(
                BASE_URL,
                auth=AUTH,
                headers=HEADERS,
                params=params,
                timeout=30
            )
        except requests.exceptions.RequestException as err:
            print(f"Network error encountered: {err}")
            break

        if response.status_code != 200:
            print(f"Failed to fetch batch at offset {offset}. HTTP {response.status_code}: {response.text}")
            break

        batch = response.json().get("result", [])
        if not batch:
            break

        all_tickets.extend(batch)
        print(f"Retrieved {len(batch)} tickets (Total: {len(all_tickets)})")

        # Stop if max_records limit is reached
        if max_records and len(all_tickets) >= max_records:
            all_tickets = all_tickets[:max_records]
            break

        # If fewer records than page_size were returned, we reached the end
        if len(batch) < page_size:
            break

        offset += page_size

    return all_tickets


# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    # Filters:
    # - active=true: Only open tasks
    # - ORDERBYDESCsys_created_on: Most recent first
    ENCODED_QUERY = "active=true^ORDERBYDESCsys_created_on"

    tickets = fetch_all_tickets(
        query_filter=ENCODED_QUERY,
        page_size=100,
        max_records=500  # Set to None to retrieve every matching ticket
    )

    print(f"\n--- Done. Successfully fetched {len(tickets)} tickets ---")

    # Display sample ticket details
    if tickets:
        sample = tickets[0]
        print("\nLatest Ticket Preview:")
        print(f"  Number:           {sample.get('number', {}).get('display_value')}")
        print(f"  Type (Class):     {sample.get('sys_class_name', {}).get('display_value')}")
        print(f"  Short Desc:       {sample.get('short_description', {}).get('display_value')}")
        print(f"  Assignment Group: {sample.get('assignment_group', {}).get('display_value')}")
        print(f"  Assigned To:      {sample.get('assigned_to', {}).get('display_value')}")
        print(f"  Created On:       {sample.get('sys_created_on', {}).get('display_value')}")
