#export DEBUG = 1
#export CIVICRM_BASE_URL = https://civicrm.evilcorp.org
#export CIVICRM_API_KEY = 1234
#export CIVICRM_SEARCHKIT_QUERIES = name_of_searchkit_query,next_search_kit_query

run:
	set -a; . ./.env; uv run src/civi_api_export/__init__.py
up:
	sudo docker compose up
build-docker:
	sudo docker build --force-rm -t civi-api-export:$$(uv version --short) .

deploy:
	git pull
	docker compose build --no-cache --pull
	sudo docker compose up -d
