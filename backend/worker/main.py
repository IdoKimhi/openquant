# Worker entry point
# This is the command run by docker-compose for the worker container

from worker.scheduler import main

if __name__ == "__main__":
    main()