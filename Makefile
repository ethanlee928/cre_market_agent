# London Office Market Monitor — container tasks.

# Docker 20.10 ships BuildKit but does not enable it by default, and the
# Dockerfile's cache and bind mounts require it. Newer Docker ignores this.
export DOCKER_BUILDKIT := 1

IMAGE ?= cre-market-agent
NAME  ?= cre-market-agent
PORT  ?= 8501

# The API key is passed in at run time, never baked into a layer. With no .env
# the app still serves the full brief; chat is the only thing that goes dark.
ENV_ARG := $(if $(wildcard .env),--env-file .env,)

.PHONY: build start stop

build:
	docker build -t $(IMAGE) .

start: build
	@docker rm -f $(NAME) >/dev/null 2>&1 || true
	docker run -d --name $(NAME) -p $(PORT):8501 $(ENV_ARG) $(IMAGE)
	@echo
	@echo '  http://localhost:$(PORT)'
	@echo

# `docker rm -f` exits 0 on a missing container, so the exit code says
# nothing. It prints the name it removed, though — that is the signal.
stop:
	@[ -n "$$(docker rm -f $(NAME) 2>/dev/null)" ] && echo stopped || echo 'not running'
