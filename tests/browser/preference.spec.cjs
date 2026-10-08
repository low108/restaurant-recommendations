const { test, expect } = require('@playwright/test');
// These UI contract tests stub the interpreter boundary; they do not verify live ILMU.
const role=(page,kind,name)=>page.getByRole(kind,{name,exact:true});
const choose=(page,kind,name)=>role(page,kind,name).locator('..').click();
async function openCheckin(page) {
  await page.goto('/');
  await role(page,'button','Sign in').click();
  await page.getByLabel('Email address',{exact:true}).fill(`${test.info().project.name}-e2e1@example.test`);
  await page.getByLabel('Password',{exact:true}).fill('local-e2e-only-709');
  await role(page,'button','Sign in').click();
  await role(page,'link','Open Nine-person E2E table').click();
  await role(page,'button','Start a meal').click();
  await role(page,'checkbox','E2E Diner 2').check();
  await role(page,'button','Invite my table to check in').click();
  await role(page,'heading','What sounds good today?').waitFor();
  await choose(page,'radio','Join');
  await role(page,'textbox','Describe your craving').fill('Light noodle soup, mild and around RM20');
}
const proposal={status:'proposed',requires_confirmation:true,remaining_requests:1,suggestions:{cuisines:['Mamak'],dish_families:['noodle_soup'],flavour_tags:['light'],spice:'mild',appetite:'light',soft_budget_target:20,occasion_features:['quiet'],novelty:'variety'}};
async function consent(page) {
  await role(page,'button','Draft my taste choices with AI').click();
  await expect(page.getByRole('dialog')).toContainText('Only the craving text shown below is sent');
  await role(page,'button','Send this text to AI').click();
}
test('AI choices need consent, review, manual edits and an ordinary check-in save',async({page})=>{
  const requests=[];
  await page.route('**/interpret-preferences',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:proposal});});
  await openCheckin(page);
  const cap=await page.getByLabel('Firm maximum today (RM per person)',{exact:true}).inputValue();
  await role(page,'button','Draft my taste choices with AI').click();
  expect(requests).toHaveLength(0);
  await role(page,'button','Cancel').click();
  await consent(page);
  await expect(page.locator('[data-preference-preview]')).toContainText('Review the AI draft');
  expect(requests).toHaveLength(1);
  expect(Object.keys(requests[0]).sort()).toEqual(['allow_model_processing','expected_response_revision','expected_revision','text']);
  expect(requests[0].allow_model_processing).toBe(true);
  await expect(page.locator('[name="taste_input_mode"]')).toHaveValue('legacy_text');
  await expect(role(page,'checkbox','Mamak')).not.toBeChecked();
  await role(page,'button','Apply draft to my choices').click();
  await expect(role(page,'checkbox','Mamak')).toBeChecked();
  await expect(role(page,'checkbox','Noodle Soup')).toBeChecked();
  await expect(role(page,'checkbox','Light')).toBeChecked();
  await expect(page.getByLabel('Firm maximum today (RM per person)',{exact:true})).toHaveValue(cap);
  await expect(role(page,'checkbox','My private food requirements are up to date for this meal.')).not.toBeChecked();
  await choose(page,'checkbox','Noodle Soup');
  await choose(page,'checkbox','Light');
  await choose(page,'checkbox','My private food requirements are up to date for this meal.');
  const saved=page.waitForRequest(request=>request.url().endsWith('/response')&&request.method()==='PUT');
  await role(page,'button','Send my check-in').click();
  expect((await saved).postDataJSON()).toMatchObject({taste_input_mode:'structured',dish_families:[],flavour_tags:[],budget:Number(cap)});
  await role(page,'button','Update my check-in').waitFor();
  await expect(page.locator('[name="taste_input_mode"]')).toHaveValue('structured');
});
test('editing a pending draft rejects its late response and preserves manual choices',async({page})=>{
  let release,captured;const waiting=new Promise(resolve=>captured=resolve);const gate=new Promise(resolve=>release=resolve);
  await page.route('**/interpret-preferences',async route=>{captured();await gate;await route.fulfill({json:proposal}).catch(()=>{});});
  await openCheckin(page);await consent(page);await waiting;
  await page.getByLabel('Spice today',{exact:true}).selectOption('hot');
  release();
  await expect(page.locator('[data-preference-preview]')).toHaveCount(0);
  await expect(page.getByLabel('Spice today',{exact:true})).toHaveValue('hot');
  await expect(role(page,'checkbox','Mamak')).not.toBeChecked();
  await expect(role(page,'button','Apply draft to my choices')).toHaveCount(0);
});
test('unavailable AI leaves the ordinary check-in usable and input clears proposals',async({page})=>{
  let calls=0;
  await page.route('**/interpret-preferences',async route=>{calls++;await route.fulfill({json:calls===1?{status:'unavailable',requires_confirmation:true,suggestions:{},remaining_requests:1}:proposal});});
  await openCheckin(page);await consent(page);
  await expect(page.locator('[data-preference-preview]')).toContainText('AI is unavailable');
  await expect(role(page,'button','Send my check-in')).toBeEnabled();
  await consent(page);
  await expect(role(page,'button','Apply draft to my choices')).toBeVisible();
  await role(page,'textbox','Describe your craving').fill('Pizza instead');
  await expect(role(page,'button','Apply draft to my choices')).toHaveCount(0);
});
test('one server-issued adaptive question is optional and saves an explicit choice',async({page})=>{
  const requests=[];
  await page.route('**/adaptive-question',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:{status:'available',request_id:'m09-test-request',question_id:'M09',question_version:'adaptive-m09-v1',prompt:'Which sounds better for this meal?',dimension:'cuisines',choices:['Chinese','Indian'],optional:true,purpose:'These choices lead to different preliminary shortlist orders.'}});});
  await page.route('**/adaptive-question/*',async route=>{
    requests.push(route.request().postDataJSON());
    const match=new URL(route.request().url()).pathname.match(/\/meals\/([^/]+)\/adaptive-question/);
    const response=await page.request.get(`/api/meals/${match[1]}`);const meal=await response.json();
    meal.revision+=1;meal.my_response_revision+=1;meal.my_response={...meal.my_response,taste_input_mode:'structured',cuisines:['Chinese']};
    await route.fulfill({json:meal});
  });
  await openCheckin(page);
  await choose(page,'checkbox','My private food requirements are up to date for this meal.');
  await role(page,'button','Send my check-in').click();
  await role(page,'button','Update my check-in').waitFor();
  await role(page,'button','Check for a useful question').click();
  await expect(page.locator('[data-adaptive-question]')).toContainText('No restaurant names or another person’s answers');
  await expect(role(page,'button','Chinese')).toBeVisible();
  expect(Object.keys(requests[0]).sort()).toEqual(['expected_response_revision','expected_revision']);
  await role(page,'button','Chinese').click();
  expect(requests[1]).toEqual({choice:'Chinese',skip:false});
  await expect(page.locator('[name="cuisines"][value="Chinese"]')).toBeChecked();
  await expect(page.locator('[name="taste_input_mode"]')).toHaveValue('structured');
  await expect(page.locator('[data-adaptive-question]')).toHaveCount(0);
});
