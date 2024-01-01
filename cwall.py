import typer
from typing import Optional
import os
import time
import yaml
import requests
import logging
import datetime
from PIL import Image
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

app = typer.Typer()

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


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
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        file = service.files().create(body=file_metadata,
                                      media_body=media,
                                      fields='id').execute()
        logging.info(
            f"Uploaded {file_path} to Google Drive with file ID {file.get('id')}"
        )
        return file.get('id')
    except HttpError as error:
        logging.error(f"An error occurred during Google Drive upload: {error}")
        return None


def get_facebook_access_token():
    print(
        f"please visit https://developers.facebook.com/tools/explorer/ and get a user access token with the following permissions: pages_show_list, business_management, instagram_basic, instagram_content_publish."
    )
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
    print(upload_params)
    upload_response = requests.post(upload_url, params=upload_params)
    if upload_response.status_code != 200:
        logging.error(
            f"Error in media upload to Facebook: {upload_response.text}")
        return None
    upload_content = upload_response.json()
    creation_id = upload_content.get("id")

    publish_url = f"https://graph.facebook.com/v18.0/{user_id}/media_publish"
    publish_params = {"creation_id": creation_id, "access_token": access_token}
    publish_response = requests.post(publish_url, params=publish_params)
    if publish_response.status_code != 200:
        logging.error(
            f"Error in media publishing to Facebook: {publish_response.text}")
        return None
    logging.info(f"Published media to Facebook with ID {creation_id}")
    return publish_response.json()


def get_upload_quota_usage(facebook_access_token, facebook_user_id):
    """Retrieves the available quota for content publishing."""
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


@app.command(
    help=
    f"Publishe raw screenshots to Google Drive in '{config['directory_path']}' directory."
)
def publish_to_google():
    directory_path = config['directory_path']
    google_drive_folder_id = config['google_drive_folder_id']
    discard = os.path.join(directory_path, "discard")
    if not os.path.exists(discard):
        os.makedirs(discard)
        logging.info(f"Created directory {discard}")
    else:
        for f in os.listdir(discard):
            os.remove(os.path.join(discard, f))

    input_files = [
        f for f in os.listdir(directory_path)
        if f.endswith((".jpg")) and f.startswith("IMG")
    ]
    for filename in input_files:
        logging.info(f"Processing file {filename}")
        file_path = os.path.join(directory_path, filename)
        JPEG_file_path = file_path.replace(".jpg", ".JPEG")
        process_image(file_path, JPEG_file_path)
        os.rename(file_path, os.path.join(directory_path, "discard", filename))
        drive_file_id = upload_to_drive(JPEG_file_path, google_drive_folder_id)
        new_filename = filename.split('.')[0] + "-" + filename.replace(
            filename, drive_file_id) + ".JPEG"
        os.rename(JPEG_file_path, os.path.join(directory_path, new_filename))

    logging.info(f"Published {len(input_files)} images to Google drive.")


@app.command(
    help=
    f"Publishes images in '{config['directory_path']}' directory to Facebook.")
def publish_to_ins():
    directory_path = config['directory_path']
    # Create a directory based on today's date
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    archive_directory = os.path.join(directory_path, "cwall_image_archive",
                                     today_date)
    if not os.path.exists(archive_directory):
        os.makedirs(archive_directory)
        logging.info(f"Created directory {archive_directory}")

    facebook_access_token = config['facebook_access_token']
    facebook_user_id = config['facebook_user_id']
    if not test_access_token(facebook_access_token):
        logging.error("Expired Facebook access token.")
        facebook_access_token = get_facebook_access_token()
    logging.info("Access token is valid.")

    used_quota = get_upload_quota_usage(facebook_access_token,
                                        facebook_user_id)
    if used_quota == -1 or used_quota == None:
        logging.error("Failed to retrieve quota.")
        return
    elif used_quota >= 50:
        logging.error("No available quota for now.")
        return
    logging.info(f"Available quota: {50 - used_quota}")

    filenames = [
        x for x in os.listdir(config['directory_path'])
        if x.endswith(".JPEG") and x.startswith("IMG")
    ]
    drive_file_ids = [
        "-".join(x.split('.')[0].split('-')[1:]) for x in filenames
    ]
    post_count = 0
    for filename, drive_file_id in zip(filenames, drive_file_ids):
        if post_count >= 50 - used_quota:
            break
        success = 0
        for i in range(3):
            result = post_to_facebook(drive_file_id, facebook_access_token,
                                      facebook_user_id)
            if result:
                logging.info(
                    f"{filename} Image posted to Facebook successfully with id: {result['id']}"
                )
                os.rename(os.path.join(config['directory_path'], filename),
                          os.path.join(archive_directory, filename))
                post_count += 1
                success = 1
                break
            else:
                logging.warning(
                    f"{filename} Failed to post to Facebook on attempt {i+1}. Retrying in 5 seconds..."
                )
                time.sleep(5)
            if not success:
                logging.error(
                    f"{filename} Failed to post to Facebook after 3 attempts. Exiting..."
                )
    logging.info(
        f"Published {post_count} posts. {len(filenames) - post_count} files remain unpublished. quota remaining: {50 - post_count - used_quota}"
    )


@app.command(help="prints content publishing limit, max 50/day")
def quota():
    facebook_access_token = config['facebook_access_token']
    facebook_user_id = config['facebook_user_id']

    if not test_access_token(facebook_access_token):
        logging.error("Expired Facebook access token.")
        facebook_access_token = get_facebook_access_token()
    print(
        f"Used quota: {get_upload_quota_usage(facebook_access_token, facebook_user_id)}"
    )


@app.command(help="runs both publish_to_google and publish_to_ins")
def run(t: int = typer.Option(
    30, help="time to sleep in between two operation.")):
    publish_to_google()
    time.sleep(t)
    publish_to_ins()


if __name__ == "__main__":
    app()
