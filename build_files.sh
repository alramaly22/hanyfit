#!/bin/bash 
set -e 
python3 -m pip install --break-system-packages -r requirements.txt 
python3 manage.py collectstatic --noinput --clear
