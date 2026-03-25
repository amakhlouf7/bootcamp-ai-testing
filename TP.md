#### Travaux Pratiques (Exercices à réaliser sur VS Code)
#### Exercice 1 : Le Gardien du Code (Instructions)
Énoncé : Modifiez votre fichier copilot-instructions.md pour ajouter une règle : "Interdire l'utilisation de var et let dans les fichiers Cypress, utiliser uniquement const".
Correction : Ajouter sous la section Cypress : - Variables : Utilise exclusivement 'const' pour garantir l'immutabilité des sélecteurs et des données de test.

#### Exercice 2 : Création d'un Expert (Agent)
Énoncé : Créez un fichier .github/agents/bruno-expert.agent.md. Cet agent doit être spécialisé dans la conversion de commandes curl en fichiers .bru.
Correction : ```yaml
name: Bruno-Expert
description: Convertit des requêtes curl en fichiers Bruno.
Tu es un expert API. Reçois une commande curl et génère le fichier .bru correspondant avec les assertions de base (status 200).


#### Exercice 3 : Le Script de Non-Régression (Prompt)
**Énoncé :** Utilisez votre commande `/automate-xray` avec le scénario suivant : 
*"Action: Cliquer sur le bouton Supprimer. Données: ID_USER=45. Résultat: L'utilisateur n'est plus visible et la DB est à jour."*
**Correction :** Vérifiez que Copilot génère bien un script **Robot Framework** (à cause de la mention DB) et une structure de page **Confluence** comme demandé dans vos instructions globales.

Bon bootcamp ! Ces exercices permettront à votre équipe de passer de spectateurs à acteurs de cette nouvelle stratégie de test.