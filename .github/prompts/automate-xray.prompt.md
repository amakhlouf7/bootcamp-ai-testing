---
name: automate-xray
description: Génère ou prépare des tests QA traçables pour Xray à partir d'un besoin ou d'un ticket.
argument-hint: "Colle un ticket, une user story ou un besoin de test"
agent: agent
---
Analyse l'entrée fournie et génère un résultat exploitable pour Xray.

Exigences :
1. Identifie le type de test à produire : manuel, Cucumber BDD, Cypress, Robot Framework ou API.
2. Structure la sortie avec un titre contenant l'ID du ticket si disponible.
3. Utilise le format suivant pour chaque cas fonctionnel :
	- Action : ...
	- Données : ...
	- Résultat attendu : ...
4. Si l'automatisation est demandée, respecte les standards du workspace : Cypress pour l'UI, Robot Framework pour le back-end/E2E, Bruno pour l'API rapide.
5. Rappelle la nécessité de générer la documentation Confluence pour toute nouvelle fonction, keyword, pattern ou technique introduit(e).
