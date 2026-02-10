import os
import json
import asyncio
import httpx
import time
import random
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

# --- CLOUD CONFIGURATION ---
SPREADSHEET_ID = '1Tazgsgl5vZ_IOn0k4QcUPAQa_SxmNjM53xfvdPzIZ64'
TARGET_GROUP = 'mgrsofusaemployees@hellofresh.com'

def get_creds():
    print("DEBUG: Entering get_creds()...")
    creds_json = os.getenv('GWS_MASTER_TOKEN_JSON')
    if not creds_json:
        print("DEBUG: ERROR - GWS_MASTER_TOKEN_JSON is empty or missing!")
        raise ValueError("GWS_MASTER_TOKEN_JSON missing.")
    
    print(f"DEBUG: Found JSON string (Length: {len(creds_json)})")
    creds_data = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_data)
    
    if creds.expired and creds.refresh_token:
        print("DEBUG: Token expired, refreshing...")
        creds.refresh(Request())
    
    print("DEBUG: Credentials successfully initialized.")
    return creds

def safe_execute(request, max_retries=5):
    for n in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                wait = (2 ** n) + (random.randint(0, 1000) / 1000)
                print(f"DEBUG: Google 503 error. Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise e
    return request.execute()

async def send_slack_dm(message):
    token = os.getenv('SLACK_BOT_TOKEN')
    email = os.getenv('SLACK_TARGET_EMAIL')
    print(f"DEBUG: Slack Setup - Token: {'YES' if token else 'NO'}, Email: {email}")
    if not token or not email: return
    async with httpx.AsyncClient() as client:
        try:
            lookup = await client.get("https://slack.com/api/users.lookupByEmail", params={"email": email}, headers={"Authorization": f"Bearer {token}"})
            user_id = lookup.json().get("user", {}).get("id")
            if user_id:
                await client.post("https://slack.com/api/chat.postMessage", json={"channel": user_id, "text": message}, headers={"Authorization": f"Bearer {token}"})
                print("DEBUG: Slack DM Sent.")
        except Exception as e:
            print(f"DEBUG: Slack API Error: {e}")

async def log_to_spreadsheet(sheets_service, added_list, removed_list, total):
    """Logs the sync results to the 'Logs' tab of the spreadsheet."""
    print("DEBUG: Attempting to update spreadsheet log in 'Logs' tab...")
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        added_count = len(added_list)
        removed_count = len(removed_list)
        added_str = ", ".join(added_list) if added_list else "None"
        removed_str = ", ".join(removed_list) if removed_list else "None"
        
        values = [[
            timestamp, 
            "SYNC_V3_CLOUD", 
            f"Added: {added_count}, Removed: {removed_count}", 
            f"Total: {total}",
            added_str,
            removed_str
        ]]
        body = {'values': values}
        safe_execute(sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Logs!A:F",
            valueInputOption="USER_ENTERED",
            body=body
        ))
        print("✅ Spreadsheet log updated successfully with email details.")
        return True
    except Exception as e:
        print(f"⚠️ Warning: Could not update spreadsheet log: {e}")
        return False

async def run_sync():
    print("==========================================")
    print("☁️ STARTING GWS CLOUD SYNC ENGINE")
    print("==========================================\n")
    
    try:
        print("STEP 1: Initializing Credentials...")
        creds = get_creds()
        directory = build('admin', 'directory_v1', credentials=creds)
        sheets = build('sheets', 'v4', credentials=creds)

        print("STEP 2: Scanning organization for USA Employees...")
        usa_employees = []
        page_token = None
        while True:
            results = safe_execute(directory.users().list(customer='my_customer', maxResults=500, projection='full', pageToken=page_token))
            users = results.get('users', [])
            for user in users:
                if user.get('suspended'): continue
                org = user.get('customSchemas', {}).get('Organization', {})
                if org.get('Account_Type') != 'Employee': continue
                country, subacc = str(org.get('Country', '')), str(org.get('SubAccount', ''))
                if country.lower() == "united states of america" or subacc.upper() == "USA":
                    m_email = next((r['value'].lower() for r in user.get('relations', []) if r['type'] == 'manager'), None)
                    if m_email:
                        usa_employees.append([user.get('name', {}).get('fullName'), user['primaryEmail'], m_email, country, subacc])
            
            page_token = results.get('nextPageToken')
            if not page_token: break
        print(f"DEBUG: Found {len(usa_employees)} USA employees.")

        print("STEP 3: Verifying active managers...")
        unique_mgr_emails = set(e[2] for e in usa_employees)
        verified_managers = []
        for m_email in unique_mgr_emails:
            try:
                m_user = safe_execute(directory.users().get(userKey=m_email))
                if not m_user.get('suspended'):
                    verified_managers.append([m_user.get('name', {}).get('fullName', 'Unknown'), m_email])
            except: continue
        print(f"DEBUG: {len(verified_managers)} managers verified.")

        print("STEP 4: Updating Group...")
        target_set = {m[1].lower() for m in verified_managers}
        current_members_map = {}
        pt = None
        while True:
            res = safe_execute(directory.members().list(groupKey=TARGET_GROUP, pageToken=pt))
            for m in res.get('members', []): current_members_map[m['email'].lower()] = m['role']
            pt = res.get('nextPageToken')
            if not pt: break

        added_emails = []
        removed_emails = []
        for m_email in target_set:
            if m_email not in current_members_map:
                try:
                    safe_execute(directory.members().insert(groupKey=TARGET_GROUP, body={'email': m_email, 'role': 'MEMBER'}))
                    added_emails.append(m_email)
                except: pass

        for m_email, role in current_members_map.items():
            if role == 'OWNER': continue
            if m_email not in target_set:
                try:
                    safe_execute(directory.members().delete(groupKey=TARGET_GROUP, memberKey=m_email))
                    removed_emails.append(m_email)
                except: pass

        final_count = len(target_set)
        
        # Log to Spreadsheet (Safe Call)
        log_success = await log_to_spreadsheet(sheets, added_emails, removed_emails, final_count)
        log_status = "✅ Logged" if log_success else "⚠️ Log Failed"

        # Prepare Slack Message
        slack_details = ""
        if added_emails: slack_details += f"\n- *Added*: {', '.join(added_emails[:10])}{'...' if len(added_emails) > 10 else ''}"
        if removed_emails: slack_details += f"\n- *Removed*: {', '.join(removed_emails[:10])}{'...' if len(removed_emails) > 10 else ''}"

        msg = f"✅ *GWS Manager Sync Complete*\n- Group: `{TARGET_GROUP}`\n- Added: {len(added_emails)}\n- Removed: {len(removed_emails)}\n- Total Members: {final_count}\n- Sheet Status: {log_status}{slack_details}"
        await send_slack_dm(msg)

    except Exception as e:
        error_msg = f"❌ *CRITICAL CLOUD FAILURE*\nError: {str(e)}"
        print(f"DEBUG: {error_msg}")
        await send_slack_dm(error_msg)

    print("\n==========================================")
    print("🏁 PROCESS FINISHED")
    print("==========================================")

if __name__ == "__main__":
    asyncio.run(run_sync())
