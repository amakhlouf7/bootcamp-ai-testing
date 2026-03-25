*** Settings ***
Library    SeleniumLibrary

*** Variables ***
${URL}                    https://opensource-demo.orangehrmlive.com
${USERNAME}               Admin
${PASSWORD}               admin123
${BROWSER}                Chrome
${TIMEOUT}                15s

# Locators
${USERNAME_FIELD}         xpath=//input[@name='username']
${PASSWORD_FIELD}         xpath=//input[@name='password']
${LOGIN_BUTTON}           xpath=//button[@type='submit']
${DASHBOARD_HEADER}       xpath=//h6[text()='Dashboard']
${ERROR_MESSAGE}          xpath=//div[contains(@class,'oxd-alert-content--error')]
${USER_DROPDOWN}          xpath=//span[@class='oxd-userdropdown-tab']

*** Test Cases ***
Valid Login Test
    [Documentation]    Test successful login with valid Admin credentials
    [Tags]    smoke    login    positive
    Open Browser To Login Page
    Input Login Credentials    ${USERNAME}    ${PASSWORD}
    Submit Login Form
    Verify Successful Login
    [Teardown]    Close Browser

Invalid Login Test
    [Documentation]    Test login failure with incorrect password
    [Tags]    login    negative
    Open Browser To Login Page
    Input Login Credentials    ${USERNAME}    wrongpassword
    Submit Login Form
    Verify Login Error Message
    [Teardown]    Close Browser

*** Keywords ***
Open Browser To Login Page
    [Documentation]    Open browser and navigate to OrangeHRM login page
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window
    Wait Until Page Contains Element    ${USERNAME_FIELD}    ${TIMEOUT}

Input Login Credentials
    [Arguments]    ${username}    ${password}
    [Documentation]    Enter username and password in login form
    Wait Until Element Is Visible    ${USERNAME_FIELD}    ${TIMEOUT}
    Input Text    ${USERNAME_FIELD}    ${username}
    Input Text    ${PASSWORD_FIELD}    ${password}

Submit Login Form
    [Documentation]    Click the login button to submit form
    Wait Until Element Is Enabled    ${LOGIN_BUTTON}    ${TIMEOUT}
    Click Button    ${LOGIN_BUTTON}

Verify Successful Login
    [Documentation]    Verify user successfully logged in
    Wait Until Page Contains Element    ${DASHBOARD_HEADER}    ${TIMEOUT}
    Page Should Contain Element    ${DASHBOARD_HEADER}
    Location Should Contain    dashboard

Verify Login Error Message
    [Documentation]    Verify error message is displayed for invalid credentials
    Wait Until Page Contains Element    ${ERROR_MESSAGE}    ${TIMEOUT}
    Page Should Contain    Invalid credentials
