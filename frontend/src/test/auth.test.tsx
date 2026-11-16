import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { bootstrap, HttpFailure, makeMe, renderApp, signedIn } from "./harness";

const unauthenticated = new HttpFailure(401, "not_authenticated", "Sign in to continue.");

describe("authentication", () => {
  it("sends a fresh installation to first-run setup and creates the administrator", async () => {
    const me = makeMe("admin");
    const { user, server } = renderApp("/", {
      ...signedIn(me),
      "GET /bootstrap": { ...bootstrap, setup_required: true },
      "GET /auth/me": unauthenticated,
      "POST /setup": me,
    });

    expect(await screen.findByText("Welcome to FETCH")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/setup");

    await user.type(screen.getByLabelText("Setup token"), "token-from-installer");
    await user.type(screen.getByLabelText("Administrator username"), "alice");
    await user.type(screen.getByLabelText("Password"), "a long passphrase");
    await user.type(screen.getByLabelText("Repeat password"), "a long passphrase");
    server.routes["GET /bootstrap"] = bootstrap;
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(server.find("POST", "/setup")[0].body).toEqual({
      setup_token: "token-from-installer",
      username: "alice",
      password: "a long passphrase",
    });
  });

  it("refuses to submit setup when the passwords differ", async () => {
    const { user, server } = renderApp("/setup", {
      "GET /bootstrap": { ...bootstrap, setup_required: true },
      "GET /auth/me": unauthenticated,
    });
    await user.type(await screen.findByLabelText("Password"), "a long passphrase");
    await user.type(screen.getByLabelText("Repeat password"), "something else");
    expect(screen.getByText("The passwords do not match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create administrator" })).toBeDisabled();
    expect(server.find("POST", "/setup")).toHaveLength(0);
  });

  it("redirects to the sign-in page, signs in and then sends the CSRF token", async () => {
    const me = makeMe("user");
    const { user, server } = renderApp("/downloads?state=failed", {
      ...signedIn(me),
      "GET /auth/me": unauthenticated,
      "POST /auth/login": me,
      "POST /auth/logout": { ok: true },
    });

    expect(await screen.findByText("Sign in to FETCH")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(screen.getByText(/Ask the owner of this server for an invitation link/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    // Returns to the page that was asked for originally.
    expect(await screen.findByRole("heading", { name: "Downloads" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/downloads");
    expect(server.find("POST", "/auth/login")[0].body).toEqual({ username: "bob", password: "correct horse battery" });

    await user.click(screen.getAllByRole("button", { name: "Account menu" })[0]);
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByText("Sign in to FETCH")).toBeInTheDocument();
    expect(server.find("POST", "/auth/logout")[0].headers["X-CSRF-Token"]).toBe("csrf-test-token");
  });

  it("shows the server's message when the credentials are wrong", async () => {
    const { user } = renderApp("/login", {
      "GET /bootstrap": bootstrap,
      "GET /auth/me": unauthenticated,
      "POST /auth/login": new HttpFailure(401, "invalid_credentials", "The username or password is not correct."),
    });
    await user.type(await screen.findByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The username or password is not correct.");
    expect(window.location.pathname).toBe("/login");
  });

  it("registers through a valid invitation link", async () => {
    const me = makeMe("user");
    const { user, server } = renderApp("/invite/abc123", {
      ...signedIn(me),
      "GET /auth/me": unauthenticated,
      "GET /auth/invitation/abc123": { valid: true, role: "user" },
      "POST /auth/register": me,
    });
    await user.type(await screen.findByLabelText("Username"), "bob");
    await user.type(screen.getByLabelText("Password"), "a long passphrase");
    await user.type(screen.getByLabelText("Repeat password"), "a long passphrase");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(server.find("POST", "/auth/register")).toHaveLength(1));
    expect(server.find("POST", "/auth/register")[0].body).toMatchObject({ token: "abc123", username: "bob" });
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it("explains an invalid invitation instead of showing the form", async () => {
    renderApp("/invite/expired", {
      "GET /bootstrap": bootstrap,
      "GET /auth/me": unauthenticated,
      "GET /auth/invitation/expired": new HttpFailure(404, "invalid_invitation", "This invitation is not valid any more."),
    });
    expect(await screen.findByText(/This invitation is not valid any more/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });

  it("tells the user when the server cannot be reached", async () => {
    renderApp("/", {
      "GET /bootstrap": new HttpFailure(502, "bad_gateway", "Bad gateway"),
      "GET /auth/me": new HttpFailure(502, "bad_gateway", "Bad gateway"),
    });
    // The client retries transport problems a couple of times before giving up.
    expect(await screen.findByText("Cannot reach the FETCH server", {}, { timeout: 12_000 })).toBeInTheDocument();
  }, 15_000);
});
