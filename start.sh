#!/bin/bash
# Render startup script for InSync

# Run database initialization (creates tables if they don't exist)
python reset_db.py --seed

# Start Uvicorn with production settings
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2

