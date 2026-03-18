---
name: db-validator
description: Vérifie l'état de la base de données après un patch ou un test d'écriture.
---
Utilise ce skill pour :
1. Valider l'intégrité des données après un Hotfix.
2. Vérifier qu'un test Robot Framework a bien écrit en base via la `DatabaseLibrary`.
3. Extraire le schéma actuel pour documenter la page **Confluence**.

Instructions : Exécute le script `check_db_integrity.py` pour obtenir un rapport de santé de la DB.