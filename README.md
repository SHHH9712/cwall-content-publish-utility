# CWALL content publish utility
This CLI tool is used for publish posts to your Instagram account.
Here we won't talk about how to setup Meta developer account and apps.

Basic usage of app
1. create and configure config.yaml
```
directory_path: /your/path/to/image/folder
facebook_access_token: xxxxx
facebook_user_id: 1234
google_drive_folder_id: xxxx
```
2. get `credentials.json` from [GCP](console.cloud.google.com/workspace-api/credentials)
3. `pip install -r requirements.txt`
4. `python cwall.py --help`
5. `python cwall.py quota`
6. `python cwall.py publish`