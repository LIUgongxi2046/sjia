// Supply credentials explicitly for the authorized local acceptance account.
export const acceptanceUsername = process.env.SJIA_ACCEPTANCE_USERNAME;
export const acceptancePassword = process.env.SJIA_ACCEPTANCE_PASSWORD;
if (!acceptanceUsername || !acceptancePassword) {
  throw new Error('SJIA_ACCEPTANCE_USERNAME and SJIA_ACCEPTANCE_PASSWORD are required');
}
