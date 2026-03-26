def main() -> None:
    print(f"Fetching {URL} ...")
    try:
        html = fetch_page()
    except Exception as e:
        print(f"ERROR: Could not fetch page: {e}", file=sys.stderr)
        sys.exit(1)

    performances = extract_performances(html)
    if not performances:
        print("WARNING: No performances found on page — page structure may have changed.")
        sys.exit(0)

    earliest = performances[0]
    print(f"Found {len(performances)} performance(s). Earliest: {earliest['name']!r} on {earliest['date_str']}")

    state = load_state()
    now = datetime.now(timezone.utc)

    # --- Alert: any new performances ---
    stored_dates = set(state.get("known_dates", []))
    current_dates = {p["date"].isoformat() for p in performances}
    new_performances = [p for p in performances if p["date"].isoformat() not in stored_dates]

    if stored_dates and new_performances:
        stored_earliest = min(date.fromisoformat(d) for d in stored_dates)
        for p in new_performances:
            if p["date"] < stored_earliest:
                subject = f"URGENT: New sooner Met Opera ticket — {p['name']} on {p['date_str']}"
            else:
                subject = f"New Met Opera student ticket added — {p['name']} on {p['date_str']}"
            body = (
                f"A new performance has appeared on the Met Opera student tickets page!\n\n"
                f"Performance: {p['name']}\n"
                f"Date: {p['date_str']}\n\n"
                f"All currently available performances:\n"
                f"{format_performance_list(performances)}\n\n"
                f"Book here: {URL}"
            )
            send_email(subject, body)
    elif not stored_dates:
        print("No stored state yet, recording current performances.")

    # --- Daily summary ---
    if should_send_daily_summary(state):
        print("Sending daily summary email...")
        subject = f"Daily Met Opera student tickets update — {now.strftime('%d %b %Y')}"
        body = (
            f"Here are all Met Opera student performances currently available:\n\n"
            f"{format_performance_list(performances)}\n\n"
            f"Book here: {URL}"
        )
        send_email(subject, body)
        state["last_daily_summary"] = now.isoformat()
    else:
        print("Daily summary already sent today, skipping.")

    # --- Save state ---
    state["known_dates"] = list(current_dates)
    state["earliest_date"] = earliest["date"].isoformat()
    state["earliest_performance"] = earliest["name"]
    state["last_checked"] = now.isoformat()
    save_state(state)
    print("State saved.")
