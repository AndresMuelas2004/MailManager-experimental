import { request } from '../client/http';
import {
  authResponseSchema,
  messageResponseSchema,
  userOutSchema,
  type AuthResponse,
  type MessageResponse,
  type UserOut,
} from '../types/dto';

export function loginWithGoogle(idToken: string): Promise<AuthResponse> {
  return request('/auth/google', {
    method: 'POST',
    body: { id_token: idToken },
    schema: authResponseSchema,
  });
}

export function loginWithMicrosoft(idToken: string): Promise<AuthResponse> {
  return request('/auth/microsoft', {
    method: 'POST',
    body: { id_token: idToken },
    schema: authResponseSchema,
  });
}

export function devLogin(): Promise<AuthResponse> {
  return request('/auth/dev-login', {
    method: 'POST',
    schema: authResponseSchema,
  });
}

export function getMe(): Promise<UserOut> {
  return request('/auth/me', { schema: userOutSchema });
}

export function logout(): Promise<MessageResponse> {
  return request('/auth/logout', { method: 'POST', schema: messageResponseSchema });
}

export function deleteMe(): Promise<MessageResponse> {
  return request('/auth/me', { method: 'DELETE', schema: messageResponseSchema });
}
