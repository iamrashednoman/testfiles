import os
import requests
from requests.auth import HTTPBasicAuth

# Instance & Credentials configuration
INSTANCE = os.getenv("SNOW_INSTANCE", "your_instance_name")
BASE_URL = f"https://{INSTANCE}.service-now.com/api/now/table/sc_task"

USERNAME = os.getenv("SNOW_USERNAME", "your_username")
PASSWORD = os.getenv("SNOW_PASSWORD", "your_password")

AUTH = HTTPBasicAuth(USERNAME, PASSWORD)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Standard fields including dot-walk to Requested For and parent item
DEFAULT_FIELDS = (
    "sys_id,number,short_description,description,state,priority,"
    "assignment_group,assigned_to,request_item,request_item.request.requested_for"
)


def get_sctask_by_sys_id(sys_id, display_value="all"):
    """
    Fetch a single catalog task record by sys_id.
    """
    url = f"{BASE_URL}/{sys_id}"
    params = {
        "sysparm_fields": DEFAULT_FIELDS,
        "sysparm_display_value": display_value
    }
    
    response = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()
    return response.json().get("result", {})


def get_sctask_by_number(ticket_number, display_value="all"):
    """
    Fetch a specific task using its human-readable ticket number (e.g. SCTASK0010025).
    """
    params = {
        "sysparm_query": f"number={ticket_number}",
        "sysparm_fields": DEFAULT_FIELDS,
        "sysparm_limit": 1,
        "sysparm_display_value": display_value
    }
    
    response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()
    results = response.json().get("result", [])
    return results[0] if results else None


def get_all_sctasks(query="", page_size=100, max_records=None, display_value="all"):
    """
    Paginate through all matching catalog tasks using sysparm_offset.
    
    :param query: ServiceNow encoded query string (e.g. 'assignment_group=<SYS_ID>^request_item.request.requested_for=<SYS_ID>')
    :param page_size: Records per HTTP request (batch size)
    :param max_records: Stop after reaching this count (None = fetch all)
    :param display_value: 'true', 'false', or 'all'
    """
    records = []
    offset = 0

    while True:
        # Calculate limit for final batch if max_records is set
        limit = page_size
        if max_records and (len(records) + page_size > max_records):
            limit = max_records - len(records)

        params = {
            "sysparm_query": query,
            "sysparm_fields": DEFAULT_FIELDS,
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": display_value
        }

        print(f"Fetching records (offset={offset}, limit={limit})...")
        response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=20)
        response.raise_for_status()
        
        batch = response.json().get("result", [])
        if not batch:
            break

        records.extend(batch)
        offset += len(batch)

        # Break condition: last page reached or reached max limit
        if len(batch) < limit or (max_records and len(records) >= max_records):
            break

    return records


# =========================================================
# Execution Example
# =========================================================
if __name__ == "__main__":
    try:
        # 1. Fetch a specific ticket by ticket number
        sample_number = "SCTASK0010001"
        print(f"\n--- 1. Fetching single task by number: {sample_number} ---")
        task = get_sctask_by_number(sample_number)
        if task:
            num = task.get("number", {}).get("display_value")
            desc = task.get("short_description", {}).get("display_value")
            req_for = task.get("request_item.request.requested_for", {}).get("display_value")
            print(f"Found: {num} | Title: {desc} | Requested For: {req_for}")
        else:
            print("No task found with that number.")

        # 2. Fetch all tasks filtered by assignment group and requested for
        # Replace these placeholders with actual 32-character sys_ids
        target_group_sys_id = "8a58cc21c611227601070be209fa821e"
        target_user_sys_id = "46d44a23a9fe19810012d100cca80666"

        encoded_query = (
            f"assignment_group={target_group_sys_id}"
            f"^request_item.request.requested_for={target_user_sys_id}"
        )

        print(f"\n--- 2. Fetching all tasks with query filter ---")
        filtered_tasks = get_all_sctasks(
            query=encoded_query,
            page_size=50,
            max_records=100
        )

        print(f"Total tasks retrieved: {len(filtered_tasks)}")
        for t in filtered_tasks[:5]:
            t_num = t.get("number", {}).get("display_value")
            t_state = t.get("state", {}).get("display_value")
            print(f" - {t_num} (State: {t_state})")

    except requests.exceptions.HTTPError as err:
        print(f"HTTP Error: {err.response.status_code} - {err.response.text}")
    except Exception as e:
        print(f"An error occurred: {e}")
