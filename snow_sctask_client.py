import requests
from requests.auth import HTTPBasicAuth

# ==========================================
# CONFIGURATION VARIABLES
# ==========================================
SNOW_INSTANCE = "your_instance_name"  # e.g., "dev12345"
SNOW_USERNAME = "your_api_username"
SNOW_PASSWORD = "your_api_password"

# Assignment group to filter by (accepts 32-char sys_id or exact group name)
ASSIGNMENT_GROUP = "Hardware Support"

BASE_URL = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/sc_task"
AUTH = HTTPBasicAuth(SNOW_USERNAME, SNOW_PASSWORD)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

FIELDS = "number,short_description,state,priority,assignment_group,assigned_to,request_item.request.requested_for"


# ==========================================
# 1. FETCH ALL SCTASKS FOR THE GROUP
# ==========================================
def get_all_sctasks_by_group(group_identifier, limit=100):
    """
    Fetch catalog tasks assigned to a specific group.
    Supports either group sys_id (32 chars) or group display name.
    """
    if len(group_identifier) == 32 and group_identifier.isalnum():
        query = f"assignment_group={group_identifier}"
    else:
        query = f"assignment_group.name={group_identifier}"

    params = {
        "sysparm_query": f"{query}^ORDERBYDESCsys_created_on",
        "sysparm_fields": FIELDS,
        "sysparm_display_value": "all",
        "sysparm_limit": limit
    }

    response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=20)
    
    if response.status_code == 200:
        return response.json().get("result", [])
    
    print(f"Error fetching group tasks [{response.status_code}]: {response.text}")
    return []


# ==========================================
# 2. FETCH SPECIFIC SCTASK IN THE GROUP
# ==========================================
def get_specific_sctask_in_group(task_number, group_identifier):
    """
    Fetch a specific catalog task by ticket number, ensuring it belongs to the group.
    """
    if len(group_identifier) == 32 and group_identifier.isalnum():
        group_filter = f"assignment_group={group_identifier}"
    else:
        group_filter = f"assignment_group.name={group_identifier}"

    params = {
        "sysparm_query": f"number={task_number}^{group_filter}",
        "sysparm_fields": FIELDS,
        "sysparm_display_value": "all",
        "sysparm_limit": 1
    }

    response = requests.get(BASE_URL, auth=AUTH, headers=HEADERS, params=params, timeout=20)

    if response.status_code == 200:
        results = response.json().get("result", [])
        return results[0] if results else None

    print(f"Error fetching specific task [{response.status_code}]: {response.text}")
    return None


# ==========================================
# USAGE
# ==========================================
if __name__ == "__main__":
    # --- 1. Fetch all tasks for the group ---
    print(f"Fetching tasks for assignment group: '{ASSIGNMENT_GROUP}'...")
    tasks = get_all_sctasks_by_group(ASSIGNMENT_GROUP, limit=10)
    print(f"Found {len(tasks)} tasks:\n")

    for task in tasks:
        num = task.get("number", {}).get("display_value")
        desc = task.get("short_description", {}).get("display_value")
        req_for = task.get("request_item.request.requested_for", {}).get("display_value")
        print(f"  [{num}] {desc} (Requested For: {req_for})")

    # --- 2. Fetch a single specific task in the group ---
    SPECIFIC_TASK_NUMBER = "SCTASK0010001"
    print(f"\nFetching single task '{SPECIFIC_TASK_NUMBER}' within '{ASSIGNMENT_GROUP}'...")
    
    single_task = get_specific_sctask_in_group(SPECIFIC_TASK_NUMBER, ASSIGNMENT_GROUP)
    if single_task:
        print(f"  Number:           {single_task.get('number', {}).get('display_value')}")
        print(f"  Short Desc:       {single_task.get('short_description', {}).get('display_value')}")
        print(f"  State:            {single_task.get('state', {}).get('display_value')}")
        print(f"  Assigned To:      {single_task.get('assigned_to', {}).get('display_value')}")
        print(f"  Requested For:    {single_task.get('request_item.request.requested_for', {}).get('display_value')}")
    else:
        print(f"  Task '{SPECIFIC_TASK_NUMBER}' not found in group '{ASSIGNMENT_GROUP}'.")
