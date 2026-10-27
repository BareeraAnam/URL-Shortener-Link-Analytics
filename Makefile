.PHONY: install migrate run worker beat test lint demo up
install:  ; pip install -r requirements-dev.txt
migrate:  ; python manage.py migrate
run:      ; python manage.py runserver
worker:   ; celery -A config worker -l info
beat:     ; celery -A config beat -l info
test:     ; pytest
lint:     ; ruff check . && ruff format --check .
demo:     ; python manage.py seed_demo
up:       ; docker compose up --build
