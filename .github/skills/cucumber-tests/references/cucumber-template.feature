# Cucumber / Gherkin Feature File Template
# File: <feature-name-kebab-case>.feature
# Tags: @smoke @regression @negative @wip

# ─── TEMPLATE ────────────────────────────────────────────────────────────────

@tag1 @tag2
Feature: [Feature Name]
  As a [role]
  I want to [action]
  So that [business value]

  Background:
    Given [shared precondition applied to all scenarios]
    And   [additional shared state]

  # --- Happy Path ---
  Scenario: [Short title — positive case]
    Given [initial context / system state]
    When  [the user performs an action]
    Then  [observable outcome]
    And   [additional assertion if needed]

  # --- Negative / Error Case ---
  @negative
  Scenario: [Short title — error case]
    Given [initial context]
    When  [the user performs an invalid action]
    Then  [error is shown / request is rejected]

  # --- Data-Driven Case ---
  Scenario Outline: [Short title — parameterized]
    Given [context with <parameter>]
    When  [action is performed with <input>]
    Then  [outcome is <expected>]

    Examples:
      | parameter     | input         | expected       |
      | value_1       | input_1       | outcome_1      |
      | value_2       | input_2       | outcome_2      |
      | edge_value    | edge_input    | edge_outcome   |


# ─── EXAMPLE — Login Feature ─────────────────────────────────────────────────

@authentication
Feature: User Login
  As a registered user
  I want to log in with my credentials
  So that I can access my personal dashboard

  Background:
    Given the application login page is displayed
    And a registered user exists with email "user@example.com" and password "Password1!"

  @smoke
  Scenario: Successful login with valid credentials
    Given the user is on the login page
    When  the user enters valid credentials
    And   the user submits the login form
    Then  the user is redirected to the dashboard
    And   the welcome message displays "Welcome, user@example.com"

  @negative
  Scenario: Login fails with incorrect password
    Given the user is on the login page
    When  the user enters email "user@example.com" and password "wrongpassword"
    And   the user submits the login form
    Then  an error message "Invalid email or password" is displayed
    And   the user remains on the login page

  @negative
  Scenario: Login fails with unregistered email
    Given the user is on the login page
    When  the user enters email "notregistered@example.com" and password "Password1!"
    And   the user submits the login form
    Then  an error message "Invalid email or password" is displayed

  @negative
  Scenario: Login form cannot be submitted with empty fields
    Given the user is on the login page
    When  the user submits the login form without entering credentials
    Then  validation messages are shown for the email and password fields

  @regression
  Scenario Outline: Login attempts with various invalid inputs
    Given the user is on the login page
    When  the user enters email "<email>" and password "<password>"
    And   the user submits the login form
    Then  the error message "<error>" is displayed

    Examples:
      | email                    | password      | error                          |
      | not-an-email             | Password1!    | Invalid email or password      |
      | user@example.com         |               | Password is required           |
      |                          | Password1!    | Email is required              |
      | user@example.com         | short         | Invalid email or password      |
