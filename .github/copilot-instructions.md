# QA Test Lead Standards (Cypress, Robot, Xray)

## 1. Stratégie de Test Hybride
- **Front-end (UI) :** Utilise exclusivement **Cypress** (TypeScript).
- **Back-end & E2E :** Utilise exclusivement **Robot Framework** (.robot).
- **API (Unit) :** Utilise **Bruno** (.bru) pour les requêtes rapides.

## 2. Standards de Code (Clean QA)
- **Cypress :** - Utilise le **Page Object Model (POM)**.
  - Sélecteurs : Priorité aux attributs `data-test` ou `data-cy`.
  - Interdiction : Pas de `cy.wait()` fixe. Utilise `cy.intercept()`.
- **Robot Framework :**
  - Respecte la séparation : Settings / Variables / Keywords / Test Cases.
  - Ajoute `[Documentation]` dans les Test Cases et les Keywords pour décrire l'objectif, les données et le résultat attendu.
  - Utilise `RequestsLibrary` pour l'API et `DatabaseLibrary` pour les patchs DB.
  - Nommage : Keywords en `Title Case`, Variables en `${SNAKE_CASE}`.

## 3. Intégration Xray (Tests Manuels)
- Toute description de test fonctionnel doit suivre le format :
  - **Action :** [Description de l'action]
  - **Données :** [Paramètres utilisés]
  - **Résultat attendu :** [Critère de succès précis]
- Ajoute toujours l'ID du ticket (ex: `[PROJ-123]`) dans le titre du test.

## 4. Gestion des Patchs & TMA
- Lors d'un "Hotfix", génère systématiquement un test de non-régression (Cypress pour l'UI, SQL pour la DB).
- Vérifie toujours l'état de la base de données après un test d'écriture.

## 5. Documentation Obligatoire (Tous Frameworks)
- Génère systématiquement une documentation **Confluence** pour toute nouvelle fonction, keyword, pattern ou technique introduit(e) dans l'automatisation.
- La page Confluence doit inclure au minimum : objectif, contexte métier, prérequis, implémentation, exemples d'utilisation, limites, et impacts CI/CD.
- Ajoute les liens de traçabilité vers les tickets (ex: Jira/Xray), les dépôts de tests et les rapports d'exécution pertinents.