# Variables
IMAGE_NAME = sportsbook-dev
CONTAINER_NAME = sportsbook-dev
WORKDIR = /workspace
PORT = 8000

# Build the dev image
build:
	podman build -t $(IMAGE_NAME) -f Containerfile .

# Run the container interactively with mounted code
run:
	podman run -it --rm \
		-v $(PWD):$(WORKDIR) \
		-p $(PORT):$(PORT) \
		--name $(CONTAINER_NAME) \
		$(IMAGE_NAME)

# Open a shell inside the running container (if detached)
shell:
	podman exec -it $(CONTAINER_NAME) bash

# Start FastAPI (runs inside container)
serve:
	uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

# Clean up local containers/images
clean:
	-podman stop $(CONTAINER_NAME)
	-podman rm $(CONTAINER_NAME)
	-podman rmi $(IMAGE_NAME)
