"""
[OHR-LOGIN-001] Browser Agent - Orange HRM Login Test
Objectif: Automatiser les tests E2E complexes avec un agent IA autonomous
Contexte: Validation du formulaire de login et du dashboard Orange HRM
Prérequis: ANTHROPIC_API_KEY définie dans .env
"""

from browser_use import Agent
from browser_use.llm import ChatAnthropic
from dotenv import load_dotenv
import asyncio
import os
import json

load_dotenv()


class OrangeHRMLoginAgent:
    """
    Agent de login pour Orange HRM.
    Utilise browser-use + ChatAnthropic pour automatiser les tests E2E.
    
    [Documentation]
    Contexte métier: Validation du workflow de login sur Orange HRM opensource demo
    Prérequis: Accès internet, navigateur Chromium via Playwright
    Implémentation: Wrapper ChatAnthropic client compatible avec browser-use
    """
    
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("❌ ANTHROPIC_API_KEY non définie dans .env")
        
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            api_key=api_key,
            temperature=0,
        )
        self.base_url = "https://opensource-demo.orangehrmlive.com"
        self.username = "Admin"
        self.password = "admin123"
    
    async def test_login_flow(self):
        """
        [PROJ-OHR-001] Test de login valide
        
        [Données] 
        - username='Admin'
        - password='admin123'
        
        [Action]
        1. Navigate login page
        2. Enter credentials
        3. Click LOGIN button
        4. Verify Dashboard
        
        [Résultat attendu]
        Dashboard affiché avec menu de navigation visible
        """
        agent = Agent(
            task=f"""
            Effectue les étapes exactes suivantes sur {self.base_url}/web/index.php/auth/login :
            
            ÉTAPES À EXÉCUTER:
            1. Navigate vers la page de login
            2. Locate le champ Username et saisis '{self.username}'
            3. Locate le champ Password et saisis '{self.password}'
            4. Locate et clique sur le bouton LOGIN
            5. Attends le chargement complet (max 10 secondes)
            6. Vérifie que l'URL change vers /dashboard
            7. Vérifie que le menu de gauche est visible
            8. Capture un screenshot du Dashboard
            
            RÉSULTAT ATTENDU:
            - Redirection vers /dashboard
            - Menu visible
            - Aucun message d'erreur
            
            Retourne SUCCÈS si toutes les étapes sont ok, ERREUR avec la raison sinon.
            """,
            llm=self.llm,
        )
        
        result = await agent.run()
        return self._parse_result(result)
    
    async def test_invalid_credentials(self):
        """
        [PROJ-OHR-002] Test de validation des credentials invalides
        
        [Données]
        - username='invalid_user'
        - password='wrong_password'
        
        [Action]
        1. Navigate login page
        2. Enter invalid credentials
        3. Click LOGIN button
        
        [Résultat attendu]
        Message d'erreur affiché, pas de redirection, reste sur /auth/login
        """
        agent = Agent(
            task=f"""
            Effectue les étapes exactes suivantes sur {self.base_url}/web/index.php/auth/login :
            
            ÉTAPES À EXÉCUTER:
            1. Navigate vers la page de login
            2. Locate le champ Username et saisis 'invalid_user'
            3. Locate le champ Password et saisis 'wrong_password'
            4. Locate et clique sur le bouton LOGIN
            5. Attends la réponse du serveur (max 5 secondes)
            6. Vérifie qu'un message d'erreur apparaît
            7. Confirme que l'URL reste sur /auth/login (pas de redirection)
            8. Capture un screenshot du message d'erreur
            
            RÉSULTAT ATTENDU:
            - Reste sur /auth/login
            - Message d'erreur visible
            - Champs toujours visibles et éditables
            
            Retourne SUCCÈS si l'erreur est détectée correctement, ERREUR sinon.
            """,
            llm=self.llm,
        )
        
        result = await agent.run()
        return self._parse_result(result)
    
    @staticmethod
    def _parse_result(result):
        """Parse et formate le résultat de l'agent."""
        if isinstance(result, dict):
            return json.dumps(result, indent=2, ensure_ascii=False)
        return str(result)


async def main():
    """
    [Documentation]
    Point d'entrée principal pour les tests Browser Agent Orange HRM.
    Exécute les scénarios de login en mode autonomous avec Claude.
    
    Limites: Dépend de la stabilité de Playwright et de la disponibilité du site
    Impacts CI/CD: À intégrer dans un stage dédié "E2E Browser Tests"
    """
    try:
        agent = OrangeHRMLoginAgent()
        
        print("=" * 60)
        print("🔄 Test 1 [PROJ-OHR-001]: Login valide avec credentials corrects")
        print("=" * 60)
        result1 = await agent.test_login_flow()
        print(f"✅ Résultat:\n{result1}\n")
        
        print("=" * 60)
        print("🔄 Test 2 [PROJ-OHR-002]: Login invalide - validation erreur")
        print("=" * 60)
        result2 = await agent.test_invalid_credentials()
        print(f"✅ Résultat:\n{result2}\n")
        
    except ValueError as e:
        print(f"❌ Erreur Configuration: {e}")
        exit(1)
    except Exception as e:
        print(f"❌ Erreur Exécution: {e}")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())