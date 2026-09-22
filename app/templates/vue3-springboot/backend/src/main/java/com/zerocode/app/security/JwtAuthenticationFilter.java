package com.zerocode.app.security;

import com.zerocode.app.common.LoginUserCache;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * JWT 认证过滤器
 */
@Slf4j
@RequiredArgsConstructor
@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtUtils jwtUtils;
    private final LoginUserCache loginUserCache;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String token = jwtUtils.getTokenFromRequest(request);
        if (StringUtils.hasText(token)) {
            Long userId = jwtUtils.getUserIdFromToken(token);
            if (userId != null && SecurityContextHolder.getContext().getAuthentication() == null) {
                LoginUser loginUser = loginUserCache.get(userId);
                if (loginUser != null) {
                    if (jwtUtils.validateToken(token) && loginUser.isEnabled()) {
                        UsernamePasswordAuthenticationToken authToken =
                                new UsernamePasswordAuthenticationToken(loginUser, null, loginUser.getAuthorities());
                        authToken.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                        SecurityContextHolder.getContext().setAuthentication(authToken);
                    }
                } else {
                    log.debug("Redis 中未找到有效登录信息, userId={}", userId);
                }
            }
        }
        filterChain.doFilter(request, response);
    }

}
