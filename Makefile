dev:
	podman-compose -f infra/compose/docker-compose.dev.yml up --build

test:
	podman-compose -f infra/compose/docker-compose.test.yml run --rm app

prod:
	podman-compose -f infra/compose/docker-compose.prod.yml up -d

down:
	podman-compose -f infra/compose/docker-compose.dev.yml down
