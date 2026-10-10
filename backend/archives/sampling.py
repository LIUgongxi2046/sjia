def sampling_groups(identities, pairs):
    groups = [{identity} for identity in identities]
    for left, right in pairs:
        matched = [group for group in groups if left in group or right in group]
        if len(matched) < 2:
            continue
        merged = set().union(*matched)
        groups = [group for group in groups if group not in matched] + [merged]
    return groups
