@echo off
REM Deploy DC Manpower Tool to Google Cloud Run
REM Run from dc-manpower directory

set CLOUDSDK_PYTHON=py.exe

echo === Copying data files into deploy directory ===
if not exist "deploy_data" mkdir deploy_data
copy "..\Layout_Productivity_Clean.xlsx" "deploy_data\" /Y
copy "..\Location_wise_Layout_data_Processing.xlsx" "deploy_data\" /Y
copy "..\Location_wise_Layout_data_Dock.xlsx" "deploy_data\" /Y
copy "..\Actual Productivity.xlsx" "deploy_data\" /Y

echo === Deploying to Cloud Run ===
gcloud run deploy dc-manpower ^
  --source . ^
  --region asia-south1 ^
  --port 8080 ^
  --memory 2Gi ^
  --cpu 2 ^
  --timeout 300 ^
  --set-env-vars ANTHROPIC_API_KEY=%ANTHROPIC_API_KEY% ^
  --allow-unauthenticated ^
  --project bi-team-400508

echo === Done ===
echo Visit the URL above to access the app
pause
