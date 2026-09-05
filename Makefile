run:
	set -a; . ./.env; uv run src/civi_api_export/__init__.py
up:
	sudo docker compose up
build-docker:
	sudo docker build --force-rm -t civi-api-export:$$(uv version --short) .

deploy:
	git pull
	sudo docker compose build --no-cache --pull
	sudo docker compose up -d

logs:
	sudo docker compose logs --tail 10 -f

restart:
	sudo docker compose restart
