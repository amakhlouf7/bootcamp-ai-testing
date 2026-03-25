---
name: code-review-tests
description: Révise le code d'automatisation de tests (Cypress, Robot Framework) selon les standards QA Lead du workspace.
argument-hint: "Sélectionne le fichier de test ou colle le code à réviser"
agent: code-interpreter
---
Effectue une révision complète du code de test automation fourni en appliquant les standards QA Lead du workspace.

## Critères de Révision

### Pour Cypress (TypeScript)
1. **Architecture**
   - Vérifie l'utilisation du Page Object Model (POM)
   - Confirme que les sélecteurs utilisent `data-test` ou `data-cy`
   - Assure qu'aucun `cy.wait()` fixe n'est présent (utilise `cy.intercept()` à la place)

2. **Bonnes Pratiques**
   - Les tests sont isolés et indépendants
   - Pas de logique métier dans les tests (déléguée aux page objects)
   - Les assertions sont claires et explicites
   - Gestion appropriée des promesses et chaînage Cypress

3. **Nommage et Lisibilité**
   - Noms de tests descriptifs (décrit le comportement métier)
   - Variables et fonctions en camelCase
   - Commentaires uniquement pour les logiques complexes

### Pour Robot Framework (.robot)
1. **Structure**
   - Respect de la séparation : Settings / Variables / Keywords / Test Cases
   - Documentation présente : `[Documentation]` dans Test Cases et Keywords
   - Documentation complète : objectif, données utilisées, résultat attendu

2. **Bonnes Pratiques**
   - Keywords nommés en `Title Case`
   - Variables en `${SNAKE_CASE}`
   - Utilisation de `RequestsLibrary` pour l'API
   - Utilisation de `DatabaseLibrary` pour les patchs DB
   - Keywords réutilisables et modulaires

3. **Clarté**
   - Pas de logique trop complexe dans un Test Case
   - Keywords bien documentés avec leur objectif
   - Séparation claire entre setup, action et vérification

### Transverse (Tous frameworks)
1. **Traçabilité Xray**
   - ID du ticket présent (ex: `[PROJ-123]`) dans le titre ou metadata
   - Documentation Confluence mentionnée ou à créer pour toute nouvelle technique

2. **Maintenabilité**
   - Code DRY (Don't Repeat Yourself)
   - Gestion des données de test externalisées
   - Pas de valeurs codées en dur (magic numbers/strings)

3. **Qualité**
   - Gestion des erreurs appropriée
   - Tests de non-régression pour les hotfix/patchs
   - Vérification de l'état DB après tests d'écriture

## Format de Sortie

Fournis un rapport structuré :

### ✅ Points Positifs
Liste les bonnes pratiques observées

### ⚠️ Points d'Amélioration
Pour chaque problème :
- **Problème :** Description claire
- **Localisation :** Ligne(s) concernée(s)
- **Impact :** Maintenabilité / Performance / Fiabilité
- **Recommandation :** Solution concrète avec exemple de code corrigé

### 📋 Checklist Conformité
- [ ] Architecture respectée (POM ou structure Robot)
- [ ] Sélecteurs/nommage conformes
- [ ] Documentation présente
- [ ] Pas de mauvaises pratiques (wait fixe, etc.)
- [ ] Traçabilité Xray
- [ ] Code maintenable et DRY

### 🎯 Score Global
Note sur 10 avec justification

### 📝 Actions Prioritaires
Les 3 actions les plus importantes à traiter en priorité
