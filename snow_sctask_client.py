import json
import requests
from requests.auth import HTTPBasicAuth

# ==========================================
# CONFIGURATION VARIABLES
# ==========================================
SNOW_INSTANCE = "your_instance_name"  # e.g., "dev12345"
SNOW_USERNAME = "your_api_username"
SNOW_PASSWORD = "your_api_password"

BASE_URL = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/sc_task"
AUTH = HTTPBasicAuth(SNOW_USERNAME, SNOW_PASSWORD)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# The fields to return in the JSON response
DEFAULT_FIELDS = (
    "sys_id,number,short_description,description,state,assignment_group,"
    "request_item,request_item.request.requested_for"
)


# ==========================================
# 1. FETCH SPECIFIC SCTASK
# ==========================================
def fetch_specific_sctask(identifier, by_number=False):
    """
    Fetch a single Catalog Task.
    :param identifier: 32-character sys_id OR ticket number (e.g. 'SCTASK0010025')
    :param by_number: Set to True if querying by 'number' instead of 'sys_id'
    """
    if by_number:
        # When querying by task number, use query filter
        params = {
            "sysparm_query": f"number={identifier}",
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_display_value": "all",
            "sysparm_limit": 1
        }
        response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=15)
        if response.status_code == 200:
            records = response.json().get("result", [])
            return records[0] if records else None
        else:
            print(f"Error [{response.status_code}]: {response.text}")
            return None
    else:
        # Direct sys_id lookup
        url = f"{BASE_URL}/{identifier}"
        params = {
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_display_value": "all"
        }
        response = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("result", {})
        else:
            print(f"Error [{response.status_code}]: {response.text}")
            return None


# ==========================================
# 2. FETCH ALL SCTASKS (WITH PAGINATION)
# ==========================================
def fetch_all_sctasks(assignment_group_sys_id=None, requested_for_sys_id=None, page_size=100, max_records=None):
    """
    Fetch all catalog tasks matching given criteria using batch pagination.
    :param assignment_group_sys_id: 32-char sys_id of assignment group (optional)
    :param requested_for_sys_id: 32-char sys_id of requested_for user (optional)
    :param page_size: Records per API call (default: 100)
    :param max_records: Cap total records returned, or None for all
    """
    query_parts = []
    if assignment_group_sys_id:
        query_parts.append(f"assignment_group={assignment_group_sys_id}")
    if requested_for_sys_id:
        query_parts.append(f"request_item.request.requested_for={requested_for_sys_id}")

    sysparm_query = "^".join(query_parts) if query_parts else "ORDERBYDESCsys_created_on"

    all_tasks = []
    offset = 0

    print(f"Starting query on sc_task with filter: '{sysparm_query}'")

    while True:
        params = {
            "sysparm_query": sysparm_query,
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_display_value": "all",
            "sysparm_limit": page_size,
            "sysparm_offset": offset
        }

        response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=20)

        if response.status_code != 200:
            print(f"Failed to fetch batch at offset {offset}. Status {response.status_code}: {response.text}")
            break

        batch = response.json().get("result", [])
        if not batch:
            break

        all_tasks.extend(batch)
        print(f"Fetched {len(batch)} records (Total so far: {len(all_tasks)})")

        if max_records and len(all_tasks) >= max_records:
            all_tasks = all_tasks[:max_records]
            break

        if len(batch) < page_size:
            # End of records
            break

        offset += page_size

    return all_tasks


# ==========================================
# EXECUTION
# ==========================================
if __name__ == "__main__":
    # --- Example 1: Fetch a specific task by number ---
    print("--- 1. Fetch Specific Task ---")
    specific_task = fetch_specific_sctask("SCTASK0010001", by_number=True)
    if specific_task:
        print("Ticket Number:", specific_task.get("number", {}).get("display_value"))
        print("Short Description:", specific_task.get("short_description", {}).get("display_value"))
        print("Description:", specific_task.get("description", {}).get("display_value"))
        print("Requested For:", specific_task.get("request_item.request.requested_for", {}).get("display_value"))
    else:
        print("Task not found or permission denied.")

    # --- Example 2: Fetch all tasks with filters ---
    print("\n--- 2. Fetch Filtered Tasks ---")
    # Leave None to query all records, or supply 32-character sys_ids:
    TARGET_GROUP_SYS_ID = None  # e.g., "8a58cc21c611227601070be209fa821e"
    TARGET_USER_SYS_ID = None   # e.g., "46d44a23a9fe19810012d100cca80666"

    results = fetch_all_sctasks(
        assignment_group_sys_id=TARGET_GROUP_SYS_ID,
        requested_for_sys_id=TARGET_USER_SYS_ID,
        page_size=50,
        max_records=100
    )

    print(f"\nRetrieved {len(results)} total SCTASKs.")
