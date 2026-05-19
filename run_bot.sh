#!/bin/bash
export PATH=/usr/local/bin:/usr/bin:/bin
cd /home/naito/ai-investor-bot || exit 1
git pull origin main
/home/naito/ai-investor-bot/venv/bin/python3 main.py --screen --notify-line
