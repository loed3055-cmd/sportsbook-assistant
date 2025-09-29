# import os
# from dotenv import load_dotenv

# load_dotenv()

# DATABASE_URL = os.getenv(
#     "DATABASE_URL",
#     "postgresql+psycopg2://sportsuser:sportspass@localhost:5432/sportsbook"
# )
import os

DATABASE_URL = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'sportsuser')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'sportspass')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:5432/"
    f"{os.getenv('POSTGRES_DB', 'sportsbook')}"
)
