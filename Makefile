fake:
	python3 fake_bus.py -s ws://127.0.0.1:8080

run_server:
	python3 server.py

test:
	pytest test_server.py

frontend_run:
	python3 -m http.server 8889

demo:
	docker-compose up --build

