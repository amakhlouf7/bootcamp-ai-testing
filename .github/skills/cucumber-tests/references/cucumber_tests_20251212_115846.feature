# language: en
@CTC2S-234
Feature: Test Suite
  Automated test cases generated from user story

  Scenario: Add a new user with valid details
    Given Navigate to the user management interface
    And Click on 'Add User' button
    And Enter valid name
    And Enter valid email address
    And Select a role from the dropdown
    Then Click 'Save' button
      # Expected Result: New user is created and a success message is displayed

  Scenario: Modify an existing user's details
    Given Navigate to the user management interface
    And Select an existing user from the list
    And Click on 'Edit' button
    And Change the user's name and/or role
    Then Click 'Save' button
      # Expected Result: User details are updated successfully and a notification is shown

  Scenario: Delete a user with confirmation
    Given Navigate to the user management interface
    And Select a user to delete
    And Click on 'Delete' button
    Then Confirm deletion in the popup dialog
      # Expected Result: User is deleted from the system and a success message is displayed

  Scenario: Attempt to add a user with invalid email
    Given Navigate to the user management interface
    And Click on 'Add User' button
    And Enter valid name
    And Enter invalid email address
    And Select a role from the dropdown
    Then Click 'Save' button
      # Expected Result: Error message 'Invalid email format' is displayed

  Scenario: Attempt to add a user without a role
    Given Navigate to the user management interface
    And Click on 'Add User' button
    And Enter valid name
    And Enter valid email address
    And Leave the role selection empty
    Then Click 'Save' button
      # Expected Result: Error message 'Role must be selected' is displayed

  Scenario: Modify user with invalid email
    Given Navigate to the user management interface
    And Select an existing user from the list
    And Click on 'Edit' button
    And Change the user's email to an invalid format
    Then Click 'Save' button
      # Expected Result: Error message 'Invalid email format' is displayed

  Scenario: Check access rights based on user role
    Given Add users with different roles (e.g., Admin, Editor, Viewer)
    And Log in as each user role
    Then Attempt to access restricted areas based on role
      # Expected Result: Each user can only access areas permitted for their role

