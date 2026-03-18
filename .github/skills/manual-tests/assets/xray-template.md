# Xray Manual Test Case Template

Use this structure for each test case exported to Jira Xray.

---

## Test Case: [TC-XXX] — [Short Summary]

| Field         | Value                                      |
|---------------|--------------------------------------------|
| **Summary**   | Verify that [expected result] when [condition] |
| **Type**      | Manual                                     |
| **Priority**  | High / Medium / Low                        |
| **Labels**    | `feature-name`, `regression`, `smoke`      |
| **Component** | [Component / Module name]                  |

### Preconditions
- [System state required before test execution]
- [User role / permissions needed]
- [Required test data / environment]

### Test Steps

| # | Step (Action) | Test Data | Expected Result |
|---|---------------|-----------|-----------------|
| 1 | [Describe the action to perform] | [Input values, if any] | [Observable outcome] |
| 2 | [Next action] | [Input values] | [Expected outcome] |
| 3 | [Continue as needed…] | | |

### Post-conditions
- [Expected system state after successful execution]

---

## Example — Login Feature

| Field         | Value                                              |
|---------------|----------------------------------------------------|
| **Summary**   | Verify that the user accesses the dashboard when valid credentials are provided |
| **Type**      | Manual                                             |
| **Priority**  | High                                               |
| **Labels**    | `authentication`, `smoke`                          |
| **Component** | Login                                              |

### Preconditions
- Application is accessible at the test environment URL
- A valid registered user account exists: `user@example.com` / `Password1!`

### Test Steps

| # | Step (Action) | Test Data | Expected Result |
|---|---------------|-----------|-----------------|
| 1 | Navigate to the login page | URL: `/login` | Login form is displayed with Email and Password fields |
| 2 | Enter a valid email address | `user@example.com` | Email field is populated |
| 3 | Enter a valid password | `Password1!` | Password field shows masked characters |
| 4 | Click the **Login** button | — | User is redirected to the dashboard |
| 5 | Verify the page title | — | Page title reads "Dashboard" |

### Post-conditions
- The user session is active
- The dashboard page is displayed

---

## Negative Example — Login Feature

| Field         | Value                                                   |
|---------------|---------------------------------------------------------|
| **Summary**   | Verify that an error message is shown when invalid credentials are provided |
| **Type**      | Manual                                                  |
| **Priority**  | High                                                    |
| **Labels**    | `authentication`, `negative`, `regression`              |
| **Component** | Login                                                   |

### Preconditions
- Application is accessible at the test environment URL

### Test Steps

| # | Step (Action) | Test Data | Expected Result |
|---|---------------|-----------|-----------------|
| 1 | Navigate to the login page | URL: `/login` | Login form is displayed |
| 2 | Enter an invalid email | `wrong@example.com` | Email field is populated |
| 3 | Enter an incorrect password | `wrongpassword` | Password field shows masked characters |
| 4 | Click the **Login** button | — | An error message is displayed: "Invalid email or password" |
| 5 | Verify no redirect occurs | — | User remains on the login page |

### Post-conditions
- No session is created
- The user remains on the login page
