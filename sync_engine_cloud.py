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

# --- CONFIGURATION (loaded from environment at runtime) ---
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
TARGET_GROUP   = os.getenv('TARGET_GROUP', '')


def get_creds():
    creds_json = os.getenv('GWS_MASTER_TOKEN_JSON')
    if not creds_json:
        raise ValueError("GWS_MASTER_TOKEN_JSON is not set.")
    creds_data = json.loads(creds_json)
    creds = Credentials.from_authorized_user_info(creds_data)
    if creds.expired and creds.refresh_token:
        print("INFO: Refreshing access token...")
        creds.refresh(Request())
    print("INFO: Credentials ready.")
    return creds


def safe_execute(request, max_retries=5):
    for n in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                wait = (2 ** n) + (random.randint(0, 1000) / 1000)
                print(f"INFO: Transient error ({e.resp.status}). Retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            raise e
    return request.execute()


async def send_slack_dm(message):
    token = os.getenv('SLACK_BOT_TOKEN')
    email = os.getenv('SLACK_TARGET_EMAIL')
    if not token or not email:
        print("INFO: Slack not configured, skipping notification.")
        return
    async with httpx.AsyncClient() as client:
        try:
            lookup = await client.get(
                "https://slack.com/api/users.lookupByEmail",
                params={"email": email},
                headers={"Authorization": f"Bearer {token}"}
            )
            user_id = lookup.json().get("user", {}).get("id")
            if user_id:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json={"channel": user_id, "text": message},
                    headers={"Authorization": f"Bearer {token}"}
                )
                print("INFO: Slack notification sent.")
        except Exception as e:
            print(f"WARN: Slack error: {type(e).__name__}")


def log_to_spreadsheet(sheets_service, added_list, removed_list, total):
    if not SPREADSHEET_ID:
        return False, "⚠️ Sheet log skipped (SPREADSHEET_ID not set)"
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = [[
            timestamp,
            "SYNC_V4_CLOUD",
            f"Added: {len(added_list)}, Removed: {len(removed_list)}",
            f"Total: {total}",
            ", ".join(added_list) if added_list else "None",
            ", ".join(removed_list) if removed_list else "None"
        ]]
        safe_execute(sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Logs!A:F",
            valueInputOption="USER_ENTERED",
            body={'values': values}
        ))
        print("INFO: Spreadsheet log updated.")
        return True, "✅ Logged"
    except HttpError as e:
        if e.resp.status == 403:
            print("WARN: Sheets log skipped — token lacks spreadsheets scope.")
            return False, "⚠️ Sheet log skipped (token scope)"
        print(f"WARN: Sheets log failed ({e.resp.status})")
        return False, f"⚠️ Sheet log failed ({e.resp.status})"
    except Exception as e:
        print(f"WARN: Sheets log error: {type(e).__name__}")
        return False, "⚠️ Sheet log error"


async def run_sync():
    print("==========================================")
    print("☁️  GWS CLOUD SYNC ENGINE v4")
    print("==========================================")

    if not TARGET_GROUP:
        raise ValueError("TARGET_GROUP environment variable is not set.")

    try:
        print("STEP 1: Initializing credentials...")
        creds     = get_creds()
        directory = build('admin', 'directory_v1', credentials=creds)
        sheets    = build('sheets', 'v4', credentials=creds)

        print("STEP 2: Scanning for USA employees...")
        usa_employees = []
        page_token = None
        while True:
            results = safe_execute(directory.users().list(
                customer='my_customer',
                maxResults=500,
                projection='full',
                pageToken=page_token
            ))
            for user in results.get('users', []):
                if user.get('suspended'):
                    continue
                org = user.get('customSchemas', {}).get('Organization', {})
                if org.get('Account_Type') != 'Employee':
                    continue
                country = str(org.get('Country', ''))
                subacc  = str(org.get('SubAccount', ''))
                if country.lower() == "united states of america" or subacc.upper() == "USA":
                    m_email = next(
                        (r['value'].lower() for r in user.get('relations', []) if r['type'] == 'manager'),
                        None
                    )
                    if m_email:
                        usa_employees.append([
                            user.get('name', {}).get('fullName'),
                            user['primaryEmail'],
                            m_email,
                            country,
                            subacc
                        ])
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        print(f"INFO: Found {len(usa_employees)} USA employees with managers.")

        print("STEP 3: Verifying active managers...")
        unique_mgr_emails = set(e[2] for e in usa_employees)
        verified_managers = []
        for m_email in unique_mgr_emails:
            try:
                m_user = safe_execute(directory.users().get(userKey=m_email))
                if not m_user.get('suspended'):
                    verified_managers.append([
                        m_user.get('name', {}).get('fullName', 'Unknown'),
                        m_email
                    ])
            except Exception:
                continue
        print(f"INFO: {len(verified_managers)} active managers verified.")

        print("STEP 4: Syncing group membership...")
        target_set = {m[1].lower() for m in verified_managers}

        current_members_map = {}
        pt = None
        while True:
            res = safe_execute(directory.members().list(groupKey=TARGET_GROUP, pageToken=pt))
            for m in res.get('members', []):
                current_members_map[m['email'].lower()] = m['role']
            pt = res.get('nextPageToken')
            if not pt:
                break

        added_emails, removed_emails = [], []

        for m_email in target_set:
            if m_email not in current_members_map:
                try:
                    safe_execute(directory.members().insert(
                        groupKey=TARGET_GROUP,
                        body={'email': m_email, 'role': 'MEMBER'}
                    ))
                    added_emails.append(m_email)
                except Exception:
                    pass

        for m_email, role in current_members_map.items():
            if role == 'OWNER':
                continue
            if m_email not in target_set:
                try:
                    safe_execute(directory.members().delete(
                        groupKey=TARGET_GROUP,
                        memberKey=m_email
                    ))
                    removed_emails.append(m_email)
                except Exception:
                    pass

        final_count = len(target_set)
        print(f"INFO: Sync done. Added: {len(added_emails)}, Removed: {len(removed_emails)}, Total: {final_count}")

        log_ok, log_status = log_to_spreadsheet(sheets, added_emails, removed_emails, final_count)

        change_lines = ""
        if added_emails:
            preview = ", ".join(added_emails[:5])
            more = f" (+{len(added_emails)-5} more)" if len(added_emails) > 5 else ""
            change_lines += f"\n• *Added ({len(added_emails)}):* {preview}{more}"
        if removed_emails:
            preview = ", ".join(removed_emails[:5])
            more = f" (+{len(removed_emails)-5} more)" if len(removed_emails) > 5 else ""
            change_lines += f"\n• *Removed ({len(removed_emails)}):* {preview}{more}"
        if not added_emails and not removed_emails:
            change_lines = "\n• No changes — group already up to date"

        msg = (
            f"✅ *GWS Manager Sync Complete*\n"
            f"• Employees scanned: {len(usa_employees)}\n"
            f"• Active managers: {len(verified_managers)}\n"
            f"• Group total: {final_count}\n"
            f"• Sheet: {log_status}"
            f"{change_lines}"
        )
        await send_slack_dm(msg)

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)}")
        await send_slack_dm(f"❌ *GWS Manager Sync FAILED*\nError: `{type(e).__name__}`")

    print("==========================================")
    print("🏁 FINISHED")
    print("==========================================")


if __name__ == "__main__":
    asyncio.run(run_sync())