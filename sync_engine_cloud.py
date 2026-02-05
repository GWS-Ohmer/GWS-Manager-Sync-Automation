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
    creds_json = os.getenv('GWS_MASTER_TOKEN_JSON')
    if not creds_json:
        raise ValueError("CRITICAL SECURITY ERROR: GWS_MASTER_TOKEN_JSON not found.")
    creds_data = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def safe_execute(request, max_retries=5):
    for n in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                wait = (2 ** n) + (random.randint(0, 1000) / 1000)
                time.sleep(wait)
                continue
            raise e
    return request.execute()

async def send_slack_dm(message):
    token = os.getenv('SLACK_BOT_TOKEN')
    email = os.getenv('SLACK_TARGET_EMAIL')
    if not token or not email: return
    async with httpx.AsyncClient() as client:
        try:
            lookup = await client.get("https://slack.com/api/users.lookupByEmail", params={"email": email}, headers={"Authorization": f"Bearer {token}"})
            user_id = lookup.json().get("user", {}).get("id")
            if user_id:
                await client.post("https://slack.com/api/chat.postMessage", json={"channel": user_id, "text": message}, headers={"Authorization": f"Bearer {token}"})
        except: pass

async def run_sync():
    print("==========================================")
    print("☁️ GWS CLOUD SYNC ENGINE V3 STARTING")
    print("==========================================\n")
    
    summary = {"added": 0, "removed": 0, "protected": 0, "errors": 0}
    audit_log = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        creds = get_creds()
        directory = build('admin', 'directory_v1', credentials=creds)
        sheets = build('sheets', 'v4', credentials=creds)

        # 1. SCAN
        usa_employees = []
        page_token = None
        while True:
            results = safe_execute(directory.users().list(customer='my_customer', maxResults=500, projection='full', pageToken=page_token))
            for user in results.get('users', []):
                if user.get('suspended'): continue
                org = user.get('customSchemas', {}).get('Organization', {})
                if org.get('Account_Type') != 'Employee': continue
                country, subacc = str(org.get('Country', '')), str(org.get('SubAccount', ''))
                if country.lower() == "united states of america" or subacc.upper() == "USA":
                    m_email = next((r['value'].lower() for r in user.get('relations', []) if r['type'] == 'manager'), None)
                    if m_email: usa_employees.append([user.get('name', {}).get('fullName'), user['primaryEmail'], m_email, country, subacc])
            page_token = results.get('nextPageToken')
            if not page_token: break

        # 2. AUDIT
        unique_mgr_emails = set(e[2] for e in usa_employees)
        verified_managers = []
        for m_email in unique_mgr_emails:
            try:
                m_user = safe_execute(directory.users().get(userKey=m_email))
                if not m_user.get('suspended'): verified_managers.append([m_user.get('name', {}).get('fullName', 'Unknown'), m_email])
            except: continue

        # 3. SPREADSHEET
        safe_execute(sheets.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range='Sheet1!A1', valueInputOption='USER_ENTERED', body={'values': [["Name", "Email", "Manager Email", "Country", "SubAccount"]] + usa_employees}))
        safe_execute(sheets.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range="'All Managers'!A1", valueInputOption='USER_ENTERED', body={'values': [["Name", "Email"]] + verified_managers}))

        # 4. SYNC
        target_set = {m[1].lower() for m in verified_managers}
        current_members_map = {}
        pt = None
        while True:
            res = safe_execute(directory.members().list(groupKey=TARGET_GROUP, pageToken=pt))
            for m in res.get('members', []): current_members_map[m['email'].lower()] = m['role']
            pt = res.get('nextPageToken')
            if not pt: break

        for m_email in target_set:
            if m_email not in current_members_map:
                try:
                    safe_execute(directory.members().insert(groupKey=TARGET_GROUP, body={'email': m_email, 'role': 'MEMBER'}))
                    summary["added"] += 1
                    audit_log.append([timestamp, "ADD", m_email, "Cloud Sync"])
                except: summary["errors"] += 1

        for m_email, role in current_members_map.items():
            if role == 'OWNER':
                summary["protected"] += 1
                continue
            if m_email not in target_set:
                try:
                    safe_execute(directory.members().delete(groupKey=TARGET_GROUP, memberKey=m_email))
                    summary["removed"] += 1
                    audit_log.append([timestamp, "REMOVE", m_email, "Cloud Sync"])
                except: summary["errors"] += 1

        if audit_log:
            safe_execute(sheets.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range="'Sync Audit Log'!A1", valueInputOption='USER_ENTERED', body={'values': audit_log}))

        msg = f"✅ *GWS Manager Sync (Cloud) COMPLETE*\n- Group: `{TARGET_GROUP}`\n- Added: {summary['added']}\n- Removed: {summary['removed']}\n- Owners Protected: {summary['protected']}"
        await send_slack_dm(msg)

    except Exception as e:
        err = f"❌ *GWS Manager Sync (Cloud) FAILED*\nError: `{str(e)}`"
        await send_slack_dm(err)

    print("\n==========================================")
    print("🏁 CLOUD PROCESS FINISHED")
    print("==========================================")

if __name__ == "__main__":
    asyncio.run(run_sync())