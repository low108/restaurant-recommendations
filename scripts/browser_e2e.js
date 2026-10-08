/* Repeatable CUA browser journey. See docs/E2E.md for fixture setup.
 * Pass two CUA tabs on 127.0.0.1 and localhost to isolate their cookies.
 * No browser-side API calls, injected application state, or fake ranker.
 */
async function runBrowserE2E(owner, friend, config) {
  const role = (tab, kind, name) => tab.playwright.getByRole(kind, {name, exact:true});
  const card = (tab) => tab.playwright.getByRole('article').filter({has:role(tab,'heading','Demo Noodle House — Fictional PJ branch')});
  const ensure = (condition, message) => { if (!condition) throw new Error(message); };
  async function login(tab, email) {
    if (await role(tab,'button','Sign out').count()) await role(tab,'button','Sign out').click();
    await role(tab,'button','Sign in').click();
    await tab.playwright.getByLabel('Email address',{exact:true}).fill(email);
    await tab.playwright.getByLabel('Password',{exact:true}).fill(config.password);
    await role(tab,'button','Sign in').click();
    await role(tab,'button','Sign out').waitFor({state:'visible'});
  }
  async function checkin(tab, craving) {
    await role(tab,'heading','What sounds good today?').waitFor({state:'visible'});
    await role(tab,'radio','Join').check();
    await role(tab,'textbox','Describe your craving').fill(craving);
    await role(tab,'checkbox','My private food requirements are up to date for this meal.').check();
    await role(tab,'button','Send my check-in').click();
    await role(tab,'button','Update my check-in').waitFor({state:'visible'});
  }
  async function vote(tab, choice, reason='') {
    await card(tab).getByRole('button',{name:choice,exact:true}).click();
    await role(tab,'textbox','Private reason (optional)').fill(reason);
    await role(tab,'button','Save my response').click();
    await tab.playwright.getByRole('dialog').waitFor({state:'hidden'});
  }
  await login(owner,config.ownerEmail);
  await role(owner,'link','Open Nine-person E2E table').waitFor({state:'visible'});
  await role(owner,'link','Open Nine-person E2E table').click();
  await role(owner,'button','Start a meal').waitFor({state:'visible'});
  await role(owner,'button','Start a meal').click();
  await role(owner,'checkbox','E2E Diner 2').check();
  await role(owner,'button','Invite my table to check in').click();
  await checkin(owner,'light soup');
  const mealHash = (await owner.url()).split('#')[1];
  await login(friend,config.friendEmail);
  await friend.goto(`http://localhost:${config.port}/#${mealHash}`);
  await checkin(friend,'anything');
  await owner.reload();
  await role(owner,'button','Find a meal together').waitFor({state:'visible'});
  await role(owner,'button','Find a meal together').click();
  await role(owner,'heading','Your table’s shortlist').waitFor({state:'visible'});
  await vote(owner,'Works for me');
  await friend.reload();
  await role(friend,'heading','Your table’s shortlist').waitFor({state:'visible'});
  if (await card(friend).getByRole('button',{name:'Report data or dietary issue'}).count()) {
    await card(friend).getByRole('button',{name:'Report data or dietary issue'}).click();
    await friend.playwright.getByLabel('Description of discrepancy').fill('Nut allergy cross-contact not mentioned on menu');
    await friend.playwright.getByRole('button',{name:'Submit report for investigation'}).click();
    await friend.playwright.getByRole('dialog').waitFor({state:'hidden'});
  }
  await vote(friend,'Cannot eat here','PRIVATE_E2E_REASON');
  await owner.reload();
  await role(owner,'heading','Your table’s shortlist').waitFor({state:'visible'});
  ensure(!await owner.playwright.getByText('PRIVATE_E2E_REASON',{exact:false}).count(),'Private reason leaked');
  ensure(!await card(owner).getByRole('button',{name:'Choose for this meal',exact:true}).isEnabled(),'Veto did not block selection');
  await vote(friend,'Works for me');
  await owner.reload();
  await role(owner,'heading','Your table’s shortlist').waitFor({state:'visible'});
  await card(owner).getByRole('button',{name:'Choose for this meal',exact:true}).click();
  await role(owner,'button','Confirm this choice').click();
  await owner.playwright.getByRole('dialog').waitFor({state:'hidden'});
  await owner.playwright.getByText('Selected for your table.',{exact:true}).waitFor({state:'visible'});
  ensure(await owner.playwright.getByText('Selected for your table.',{exact:true}).isVisible(),'Decision was not published');
  return {status:'passed',mealHash,checks:['large-room subset','independent private check-ins','actual graph/ranker','dietary data error report','private veto','unanimous selection']};
}
