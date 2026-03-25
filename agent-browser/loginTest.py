from browser_use import Agent
from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv
import asyncio

load_dotenv()

async def test_login():
    agent = Agent(
        task="""
        1. Va sur https://opensource-demo.orangehrmlive.com/web/index.php/auth/login
        2. Saisis 'Admin' dans Username
        3. Saisis 'admin123' dans Password
        4. Clique sur LOGIN
        5. Vérifie le Dashboard
        """,
        llm=ChatAnthropic(model="claude-sonnet-4-20250514")
    )
    result = await agent.run()
    print(f"✅ Résultat: {result}")

asyncio.run(test_login())

