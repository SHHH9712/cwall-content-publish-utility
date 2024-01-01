import typer
from typing import Optional
import os
import time
import yaml
import requests
import logging
import datetime
from PIL import Image
from flask import Flask, request, redirect
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

app = typer.Typer()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Image Processing
def make_square(im, fill_color=(0, 0, 0)):
    x, y = im.size
    size = max(x, y)
    new_im = Image.new('RGB', (size, size), fill_color)
    new_im.paste(im, ((size - x) // 2, (size - y) // 2))
    return new_im

def process_image(file_path, save_path):
    im = Image.open(file_path)
    im = make_square(im)
    im.save(save_path)
    logging.info(f"Processed image saved as {save_path}")

def load_config(yaml_file):
    with open(yaml_file, 'r') as file:
        return yaml.safe_load(file)

config = load_config("config.yaml")
# Google Drive Upload
SCOPES = ["https://www.googleapis.com/auth/drive"]

def upload_to_drive(file_path, folder_id):
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {'name': os.path.basename(file_path), 'parents': [folder_id]}
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        logging.info(f"Uploaded {file_path} to Google Drive with file ID {file.get('id')}")
        return file.get('id')
    except HttpError as error:
        logging.error(f"An error occurred during Google Drive upload: {error}")
        return None

def get_facebook_access_token():
    print(f"please visit https://developers.facebook.com/tools/explorer/ and get a user access token with the following permissions: pages_show_list, business_management, instagram_basic, instagram_content_publish.")
    access_token = input("enter access token:")
    with open("config.yaml", 'r') as file:
        config = yaml.safe_load(file)
        config['facebook_access_token'] = access_token
    with open("config.yaml", 'w') as file:
        yaml.dump(config, file)
    return access_token
    
def test_access_token(access_token):
    test_url = f"https://graph.facebook.com/v18.0/me?access_token={access_token}"
    response = requests.get(test_url)
    if response.status_code != 200:
        logging.error(f"Error in testing access token: {response.text}")
        return False
    return True
    
# Facebook Graph API Upload
def post_to_facebook(drive_file_id, access_token, user_id):
    image_url = f"https://drive.google.com/uc?id={drive_file_id}"
    upload_url = f"https://graph.facebook.com/v18.0/{user_id}/media"
    upload_params = {"image_url": image_url, "access_token": access_token}
    upload_response = requests.post(upload_url, params=upload_params)
    if upload_response.status_code != 200:
        logging.error(f"Error in media upload to Facebook: {upload_response.text}")
        return None
    upload_content = upload_response.json()
    creation_id = upload_content.get("id")

    publish_url = f"https://graph.facebook.com/v18.0/{user_id}/media_publish"
    publish_params = {"creation_id": creation_id, "access_token": access_token}
    publish_response = requests.post(publish_url, params=publish_params)
    if publish_response.status_code != 200:
        logging.error(f"Error in media publishing to Facebook: {publish_response.text}")
        return None
    logging.info(f"Published media to Facebook with ID {creation_id}")
    return publish_response.json()

def get_upload_quota_usage():
    """Retrieves the available quota for content publishing."""
    facebook_access_token = config['facebook_access_token']
    facebook_user_id = config['facebook_user_id']
    
    if not test_access_token(facebook_access_token):
        logging.error("Expired Facebook access token.")
        facebook_access_token = get_facebook_access_token()

    url = f"https://graph.facebook.com/v18.0/{facebook_user_id}/content_publishing_limit"
    params = {'access_token': facebook_access_token}
    
    try:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            logging.error(f"Error in retrieving quota: {response.text}")
            return
        return response.json()['data'][0]['quota_usage']
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return

@app.command(help=f"Publishe raw screenshots in '{config['directory_path']}' directory.")
def publish():
    directory_path = config['directory_path']
    google_drive_folder_id = config['google_drive_folder_id']
    facebook_access_token = config['facebook_access_token']
    facebook_user_id = config['facebook_user_id']
    
    if not test_access_token(facebook_access_token):
        logging.error("Expired Facebook access token.")
        facebook_access_token = get_facebook_access_token()
    
    
    used_quota = get_upload_quota_usage()
    input_files = [f for f in os.listdir(directory_path) if f.endswith((".jpg", ".JPG", ".JPEG", ".JPEG")) and f.startswith("IMG")]
    
    if used_quota == -1 or used_quota == None:
        logging.error("Failed to retrieve quota.")
        return
    available_quota = 50 - used_quota
    
    file_count = len(input_files)
    
    if available_quota <= 0:
        logging.error("No available quota for today.")
        return
    
    logging.info(f"Available quota: {available_quota}")
    
    # Create a directory based on today's date
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    archive_directory = os.path.join('', "image_archive", today_date)
    if not os.path.exists(archive_directory):
        os.makedirs(archive_directory)
        logging.info(f"Created directory {archive_directory}")
    if len(os.listdir(archive_directory)) > 50:
        logging.error("today's post limit reached")
        return

    drive_file_ids = []
    post_count = 0
    for filename in input_files:
        if post_count >= available_quota:
            break
        file_path = os.path.join(directory_path, filename)
        logging.info(f"Processing file {file_path}")

        # Save processed file in the archive directory
        processed_file_name = filename.replace(".jpg", "_square.JPEG")
        processed_file_path = os.path.join(archive_directory, processed_file_name)
        process_image(file_path, processed_file_path)
        drive_file_id = upload_to_drive(processed_file_path, google_drive_folder_id)
        drive_file_ids.append((drive_file_id, filename))
    if len(drive_file_ids) == 0:
        logging.info("No files to upload to google drive. Exiting...")
        return
    logging.info(f"Uploaded {len(drive_file_ids)} files to Google Drive. Waiting 30 seconds before posting to Facebook...")
    time.sleep(30)
    for drive_file_id, filename in drive_file_ids:
        success = 0
        for i in range(3):
            result = post_to_facebook(drive_file_id, facebook_access_token, facebook_user_id)
            if result:
                logging.info(f"{filename} Image posted to Facebook successfully with id: {result['id']}")
                os.remove(file_path)
                post_count += 1
                success = 1
                break
            else:
                logging.warning(f"{filename} Failed to post to Facebook on attempt {i+1}. Retrying in 5 seconds...")
                time.sleep(5)
            if not success:
                logging.error(f"{filename} Failed to post to Facebook after 3 attempts. Exiting...")
    logging.info(f"Published {post_count} posts. {file_count - post_count} files remain unprocessed.")

@app.command(help="prints content publishing limit, max 50/day")
def quota():
    print(f"Used quota: {get_upload_quota_usage()}")

if __name__ == "__main__":
    app()
