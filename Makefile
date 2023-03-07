test:
	docker compose run --rm run_tests

demo:
	docker compose up -d

start:
	docker compose up -d --build

stop
  docker compose down --remove-orphans

lint
  docker compose run --rm lint

