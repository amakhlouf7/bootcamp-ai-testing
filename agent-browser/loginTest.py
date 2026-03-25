from browser_use import Agent
from anthropic import Anthropic
from dotenv import load_dotenv
import asyncio
import os

load_dotenv()

async def test_login():
    # Utilisation directe du client Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    
    agent = Agent(
        task="""
        1. Va sur https://opensource-demo.orangehrmlive.com/web/index.php/auth/login
        2. Saisis 'Admin' dans Username
        3. Saisis 'admin123' dans Password
        4. Clique sur LOGIN
        5. Vérifie le Dashboard
        """,
        llm=client
    )
    result = await agent.run()
    print(f"✅ Résultat: {result}")

asyncio.run(test_login())

