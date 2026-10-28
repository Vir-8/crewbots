/** The profile replaces an open thread or page; closing either normally leaves the preference alone. */
export function toggleProfile(profileOn: boolean, otherPanelOpen: boolean) {
  return { profileOn: otherPanelOpen || !profileOn, thread: null, page: null };
}

export function profileVisible(profileOn: boolean, threadOpen: boolean, pageOpen: boolean): boolean {
  return profileOn && !threadOpen && !pageOpen;
}
